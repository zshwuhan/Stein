import numpy as np
import tensorflow as tf
from mpi4py import MPI
from .abstract_stein_sampler import AbstractSteinSampler
from .stein_sampler import SteinSampler
from ..utilities import convert_dictionary_to_array, convert_array_to_dictionary


class ParallelSteinSampler(AbstractSteinSampler):
    """Parallel Stein Sampler Class

    The parallel Stein sampler class exploits the fact that we can obtain a
    stochastic approximation to the Stein variational gradient by simply
    considering subsets of particles. This allows us to substantively decease
    the number of particles used in an O(n*n) computation. We leverage MPI for
    communicating particles across processes so that we do not observe particles
    collapsing to the same points.
    """
    def __init__(self, n_particles, n_shuffle, log_p, gd, theta=None):
        """Initialize the parameters of the parallel Stein sampler object.

        Parameters:
            n_particles (int): The number of particles to use in the algorithm.
                This is equivalently the number of samples to generate from the
                target distribution.
            n_shuffle (int): The number of gradient descent iterations to
                perform before shuffling the particles between the consituent
                processes. This prevents the particles from collapsing to
                identical values.
            log_p (TensorFlow tensor): A TensorFlow object corresponding to the
                log-posterior distribution from which parameters wish to be
                sampled. We only need to define the log-posterior up to an
                addative constant since we'll simply take the gradient with
                respect to the inputs and this term will vanish.
            gd (AbstractGradientDescent): An object that inherits from the
                abstract gradient descent object defined within the Stein
                library. This class is used to determine how to perturb the
                particles once the optimal perturbation direction. For instance,
                we might choose to update the particles according to the Adam
                optimizer scheme.
            theta (numpy array, optional): An optional parameter corresponding
                to the initial values of the particles. The dimension of this
                array (if it is provided) should be the number of particles by
                the number of random variables (parameters) to be sampled. If
                this value is not provided, then the initial particles will be
                generated by sampling from a multivariate standard normal
                distribution.
        """
        # Use MPI for communication between parallel samplers.
        self.comm = MPI.COMM_WORLD
        self.n_particles = n_particles
        self.n_shuffle = n_shuffle
        self.n_workers = self.comm.size
        self.particles_per_worker = n_particles // self.n_workers
        if self.n_particles % self.n_workers != 0 and self.comm.rank == 0:
            raise ValueError(
                "The number of particles must be divisible by the number of "
                "worker processes."
            )

        # Partition the particles among the worker processes. If there was no
        # provided set of initial particles, then we can just initialize the
        # sampler via the default constructor.
        if theta is None:
            self.sampler = SteinSampler(self.particles_per_worker, log_p, gd)
        else:
            idx = self.comm.rank*self.particles_per_worker
            self.sampler = SteinSampler(
                self.particles_per_worker,
                log_p,
                gd,
                {
                    v: x[idx:idx+self.particles_per_worker]
                    for v, x in theta.items()
                }
            )

    def function_posterior_distribution(self, func, feed_dict):
        """Implementation of abstract base class method."""
        # Merge together all of the particles from all constituent processes.
        theta = self.merge()
        # Do all the processing on the master process since it has all of the
        # particles.
        if self.is_master:
            # Initialize a vector to store the value of the function for each
            # sample from the Bayesian posterior.
            dist = np.zeros((self.n_particles, ))
            # Iterate over each particle and compute the value of the function.
            for i in range(self.n_particles):
                feed_dict.update({v: x[j] for v, x in self.theta.items()})
                dist[i] = self.sampler.sess.run(func, feed_dict)

            return dist

    def train_on_batch(self, batch_feed):
        """Implementation of abstract base class method."""
        self.sampler.train_on_batch(batch_feed)
        # Shuffle the particles to prevent them from collapsing to identical
        # values.
        if self.sampler.gd.n_iters % self.n_shuffle == 0:
            self.shuffle()

    def merge(self):
        """This method assembles the particles from all of the worker processes
        and returns a dictionary mapping TensorFlow variables to the
        corresponding value of each particle.

        Returns:
            Dict: A dictionary mapping TensorFlow variables to matrices where
                each row is a particle and each column is a parameter for that
                variable.
        """
        # Every process converts its TensorFlow dictionary of parameters into a
        # numpy array and saves the indices for how to reconstruct the
        # dictionary.
        theta_array, access = convert_dictionary_to_array(self.sampler.theta)

        if self.is_master:
            # The master process requests each worker to send it a numpy
            # representation of its particles. The concatenated numpy
            # representation is then mapped back to a dictionary.
            for i in range(1, self.n_workers):
                theta_array = np.vstack((
                    theta_array, self.comm.recv(source=MPI.ANY_SOURCE)
                ))
            return convert_array_to_dictionary(theta_array, access)
        else:
            # Every worker process sends the master process a numpy
            # representation of its particles.
            self.comm.send(theta_array, dest=0)

    def shuffle(self):
        """This method shuffles the particles amongst the processes. This allows
        us to enforce the idea that each worker's particles should not collapse
        to the same sample. The idea is to enforce diversity by periodically
        transmitting particles to randomly selected destination processes, where
        the repulsive effect of the kernel will be different.
        """
        # Merge together all the particles. Notice that for all processes except
        # the master node this returns none.
        theta = self.merge()

        if self.is_master:
            # Create an assignment of the destination for each particle.
            a = np.random.permutation(self.n_particles)
            assign = np.reshape(a, (self.n_workers, self.particles_per_worker))
            # Create a big array of all the particles.
            theta_array, access = convert_dictionary_to_array(theta)
            for i in range(1, self.n_workers):
                self.comm.send(theta_array[assign[i]], dest=i)
            theta_array = theta_array[assign[0]]
        else:
            # Remember access indices so that when we obtain new particles we
            # know how to interpret them.
            _, access = convert_dictionary_to_array(self.sampler.theta)
            # Receive the destinations for the particles from the master process.
            theta_array = self.comm.recv(source=0)

        # Convert the numpy array back to a dictionary using the saved access
        # indices.
        self.sampler.theta = convert_array_to_dictionary(theta_array, access)

    @property
    def is_master(self):
        """This is a boolean property that reflects whether or not the process
        with a given rank is the master process. The master process is defined
        to be the process with a rank of zero.

        Returns:
            Boolean: Whether or not the current process is the master process.
        """
        return self.comm.rank == 0
