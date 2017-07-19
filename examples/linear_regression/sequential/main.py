import numpy as np
import tensorflow as tf
import matplotlib.pyplot as plt
from time import time
from tensorflow.contrib.distributions import Normal, Gamma
from stein.samplers import SteinSampler
from stein.gradient_descent import AdamGradientDescent


# Keep track of time elapsed.
start = time()
# Import data.
data_X = np.loadtxt("../data/data_X.csv", delimiter=",")
data_w = np.atleast_2d(np.loadtxt("../data/data_w.csv", delimiter=",")).T
data_y = np.atleast_2d(np.loadtxt("../data/data_y.csv", delimiter=",")).T
n_samples, n_feats = data_X.shape

with tf.variable_scope("model"):
    # Placeholders for features and targets.
    model_X = tf.placeholder(tf.float32, shape=[None, n_feats])
    model_y = tf.placeholder(tf.float32, shape=[None, 1])
    model_w = tf.Variable(tf.zeros([n_feats, 1]))
    # Compute prior.
    with tf.variable_scope("priors"):
        w_prior = Normal(tf.zeros([n_feats, 1]), 1.)
    # Compute likelihood function.
    with tf.variable_scope("likelihood"):
        y_hat = tf.matmul(model_X, model_w)
        log_l = -0.5 * tf.reduce_sum(tf.square(y_hat - model_y))
    # Compute the log-posterior of the model.
    log_p = log_l + tf.reduce_sum(w_prior.log_prob(model_w))


# Number of learning iterations.
n_iters = 1000
n_prog = n_iters // 10
# Sample from the posterior using Stein variational gradient descent.
n_particles = 50
gd = AdamGradientDescent(learning_rate=1e-1)
sampler = SteinSampler(n_particles, log_p, gd)
# Perform learning iterations.
for i in range(n_iters):
    if i % n_prog == 0:
        print("Iteration: {} / {}".format(i, n_iters))
    sampler.train_on_batch({model_X: data_X, model_y: data_y})

# Show diagnostics.
est = np.array(list(sampler.theta.values()))[0].mean(axis=0).ravel()
print("True coefficients: {}".format(data_w.ravel()))
print("Est. coefficients: {}".format(est))
print("Time elapsed: {}".format(time() - start))