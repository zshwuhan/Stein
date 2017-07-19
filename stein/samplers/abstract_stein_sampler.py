import tensorflow as tf
import numpy as np
from abc import ABCMeta, abstractmethod
from ..kernels import SquaredExponentialKernel
from ..utilities import convert_array_to_dictionary, convert_dictionary_to_array


class AbstractSteinSampler(object):
    """Abstract Stein Sampler Class

    This class implements the algorithm from the paper "Stein Variational
    Gradient Descent: A General Purpose Bayesian Inference Algorithm" by Liu and
    Wang. This algorithm provides a mechanism for sampling from arbitrary
    distributions provided that the gradient of the distribution can be computed
    with respect to the input.

    The Stein variational gradient descent algorithm seeks to identify the
    optimal perturbation direction for a set of particles that will be
    iteratively transformed such that the empirical distribution of the
    particles can be seen to approximate a sample from the distribution. This is
    achieved by minimizing the KL-divergence between the samples and the target
    distribution, the optimal decrease direction for which can be obtained in
    closed-form and approximated via sampling. In particular, we compute the
    direction of greatest decrease subject to a set of functions of bounded norm
    within a reproducing kernel Hilbert space (RKHS).
    """
    def __init__(self, n_particles, log_p, gd, theta=None):
        """Initialize the parameters of the abstract Stein sampler object.

        Parameters:
            n_particles (int): The number of particles to use in the algorithm.
                This is equivalently the number of samples to generate from the
                target distribution.
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
        # Number of particles to use during sampling.
        self.n_particles = n_particles
        # Construct a squared exponential kernel for computing the repulsive
        # force between particles.
        self.kernel = SquaredExponentialKernel()
        # Gradient descent object will determine how particles are updated.
        self.gd = gd

        # Construct a TensorFlow session.
        self.sess = tf.Session()
        self.model_vars = tf.get_collection(
            tf.GraphKeys.TRAINABLE_VARIABLES, "model"
        )
        # Create class variables for the log-posterior and the gradient of the
        # log-posterior with respect to model parameters.
        self.log_p = log_p
        self.grad_log_p = tf.gradients(self.log_p, self.model_vars)

        # If particles are provided, then use them. Otherwise, particles are
        # initialized by sampling from a standard normal distribution. This
        # latter method works well for relatively simple models, but better
        # initialization is required for complex distributions such as those
        # that are parametrized by neural networks.
        if theta is not None:
            self.theta = theta
        else:
            # Notice that `theta` is a dictionary that maps model parameters to
            # a matrix representing the value of that parameter for each of the
            # particles.
            self.theta = {
                v: np.random.normal(
                    size=[self.n_particles] + v.get_shape().as_list()
                )
                for v in self.model_vars
            }

    def compute_phi(self, theta_array, grads_array):
        """Assuming a reproducing kernel Hilbert space with associated kernel,
        this function computes the optimal perturbation in the particles under
        functions in the unit ball under the norm of the RKHS. This perturbation
        can be regarded as the direction that will maximally decrease the
        KL-divergence between the empirical distribution of the particles and
        the target distribution.

        Parameters:
            theta_array (numpy array): A two-dimensional matrix with dimensions
                equal to the number of particles by the number of parameters.
                This is the matrix representation of the particles.
            grads_array (numpy array): A two-dimensional matrix with dimensions
                equal to the number of particles by the number of parameters.
                This is the matrix representation of the gradient of the
                log-posterior with respect to the particles.

        Returns:
            Numpy array: A two-dimensional matrix with dimensions equal to the
                number of particles by the number of parameters. This is the
                update value corresponding to the optimal perturbation direction
                given by Stein variational gradient descent.
        """
        # Extract the number of particles and number of parameters.
        n_particles, n_params = grads_array.shape
        # Compute the kernel matrices and gradient with respect to the
        # particles.
        K, dK = self.kernel.kernel_and_grad(theta_array)

        return (K.dot(grads_array) + dK) / n_particles

    def update_particles(self, grads_array):
        """Internal method that computes the optimal perturbation direction
        given the current set of particles and the gradient of the
        log-posterior. Notice that this method applies the gradient descent
        update and normalizes the gradient to have a given norm. Computation of
        the optimal perturbation direction is broken out into the method
        `compute_phi`.

        Parameters:
            grads_array (numpy array): A numpy array mapping TensorFlow model
                variables to the gradient of the log-posterior.
        """
        # Convert both the particle dictionary and the gradient dictionary into
        # vector representations.
        theta_array, access_indices = convert_dictionary_to_array(self.theta)
        # Compute optimal update direction.
        phi = self.compute_phi(theta_array, grads_array)
        # Normalize the gradient have be norm no larger than the desired amount.
        phi *= 10. / max(10., np.linalg.norm(phi))
        theta_array += self.gd.update(phi)
        self.theta = convert_array_to_dictionary(theta_array, access_indices)

    @abstractmethod
    def train_on_batch(self, batch_feed):
        """Trains the Stein variational gradient descent algorithm on a given
        batch of data (provided in the form of a TensorFlow feed dictionary).
        This function computes the gradient of the log-likelihood for each
        sampling particle and then computes the optimal perturbation using the
        formula provided in the Stein variational gradient descent paper. Notice
        that, like the particles themselves, the gradients are represented as
        dictionaries that allow to keep the gradients distinct for each
        parameter.

        Notice that this function does not update the TensorFlow variables used
        to define the model. This wouldn't make sense in the first place since
        there are multiple particles corresponding to a random draw of those
        variables. Instead, the `theta` class variable is a dictionary that
        stores all of the particle values for each parameter in the model.

        Parameters:
            batch_feed (dict): A dictionary that maps TensorFlow placeholders to
                provided values. For instance, this might be mappings of feature
                and target placeholders to batch values. Notice that this feed
                dictionary will be internally augmented to include the current
                feed values for the model parameters for each particle.
        """
        raise NotImplementedError()


