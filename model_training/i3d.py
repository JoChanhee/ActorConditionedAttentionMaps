# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Inception-v1 Inflated 3D ConvNet used for Kinetics CVPR paper.

The model is introduced in:

  Quo Vadis, Action Recognition? A New Model and the Kinetics Dataset
  Joao Carreira, Andrew Zisserman
  https://arxiv.org/pdf/1705.07750v1.pdf.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sonnet as snt
import tensorflow as tf

##### Wrapper Begins
# WEIGHTS_PATH = './models/weights/'
# import os
# MAIN_FOLDER = os.environ['AVA_DIR']
# WEIGHTS_PATH = MAIN_FOLDER + '/model_training/models/weights/'

def inference(input_image, is_training, num_classes, end_point='Logits', channel_mult=1.0, lateral=False):
  dropout_keep = tf.cond(is_training, lambda: 0.2, lambda: 1.0)
  processed_input = preprocess(input_image)
  with tf.variable_scope('I3D_Model'):
    if not lateral:
        model = InceptionI3d(num_classes, spatial_squeeze=True, final_endpoint=end_point)
    else:
        model = LateralInceptionI3d(num_classes, spatial_squeeze=True, final_endpoint=end_point)

    logits, endpoints = model(processed_input, is_training=is_training, dropout_keep_prob=dropout_keep, channel_mult=channel_mult)

  return logits, endpoints

def preprocess(input_seq):
  # crop_mean = np.load(WEIGHTS_PATH + 'c3d_crop_mean.npy')
  # output_seq = input_seq - crop_mean
  
  # scale between [-1,+1]
  output_seq = input_seq / 128.0 - 1.0

  # scale
  # output_seq = output_seq / 255.0
  return output_seq

def initialize_weights(sess, path_to_weights):
  i3d_var = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='I3D_Model')
  var_map = {}
  for variable in i3d_var:
      # if variable.name.endswith('w:0') or variable.name.endswith('beta:0'):
      if 'Adam' not in variable.name:
        if 'lateral' not in variable.name:
          map_name = variable.name.replace(':0', '')
          map_name = map_name.replace('I3D_Model', 'RGB')
          var_map[map_name] = variable
      
  rgb_saver = tf.train.Saver(var_list=var_map, reshape=True)

  #path_to_weights = weights_path + 'i3d_rgb_imagenet/model.ckpt'
  rgb_saver.restore(sess, path_to_weights)
  print('Restored i3d head weights from %s ' % path_to_weights)

def initialize_tail(sess, weights_path):
  # weights_path = MAIN_FOLDER + '/model_training/models/weights/'
  # path_to_weights = weights_path + 'i3d_rgb_imagenet/model.ckpt'
  ## need var_map keys as this
  # RGB/inception_i3d/Mixed_3c/Branch_2/Conv3d_0b_3x3/conv_3d/w

  tail_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Tail_I3D')

  var_map = {}
  for variable in tail_vars:
     # if variable.name.endswith('w:0') or variable.name.endswith('beta:0'):
     if 'Adam' not in variable.name:
          map_name = variable.name.replace(':0', '')
          map_name = map_name.replace('Tail_I3D', 'RGB/inception_i3d')
          var_map[map_name] = variable
  if var_map.keys():
      tail_saver = tf.train.Saver(var_list=var_map, reshape=True)
      tail_saver.restore(sess, weights_path)
      print('Restored i3d tail weights from %s ' % weights_path)
  else:
      print('Tail did not initialize anything')
  
def initialize_all_i3d_from_ckpt(sess, ckpt_file):
  head_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='RGB/inception_i3d')
  tail_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Tail_I3D')
  cls_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='CLS_Logits')

  var_list = head_vars + tail_vars + cls_vars
  i3d_loader = tf.train.Saver(var_list=var_list)
  i3d_loader.restore(sess, ckpt_file)

  print('Restored I3D head - tail and CLS_Logits from ckpt %s' % ckpt_file)

###### Wrapper Ends


class Unit3D(snt.AbstractModule):
  """Basic unit containing Conv3D + BatchNorm + non-linearity."""

  def __init__(self, output_channels,
               kernel_shape=(1, 1, 1),
               stride=(1, 1, 1),
               activation_fn=tf.nn.relu,
               use_batch_norm=True,
               use_bias=False,
               name='unit_3d'):
    """Initializes Unit3D module."""
    super(Unit3D, self).__init__(name=name)
    self._output_channels = output_channels
    self._kernel_shape = kernel_shape
    self._stride = stride
    self._use_batch_norm = use_batch_norm
    self._activation_fn = activation_fn
    self._use_bias = use_bias

  def _build(self, inputs, is_training):
    """Connects the module to inputs.

    Args:
      inputs: Inputs to the Unit3D component.
      is_training: whether to use training mode for snt.BatchNorm (boolean).

    Returns:
      Outputs from the module.
    """
    net = snt.Conv3D(output_channels=self._output_channels,
                     kernel_shape=self._kernel_shape,
                     stride=self._stride,
                     padding=snt.SAME,
                     use_bias=self._use_bias)(inputs)
    if self._use_batch_norm:
      bn = snt.BatchNorm()
      #################### Warning batchnorm is hard coded to is_training=False #################
      # net = bn(net, is_training=is_training, test_local_stats=False)
      net = bn(net, is_training=False, test_local_stats=False)
    if self._activation_fn is not None:
      net = self._activation_fn(net)
    return net


class InceptionI3d(snt.AbstractModule):
  """Inception-v1 I3D architecture.

  The model is introduced in:

    Quo Vadis, Action Recognition? A New Model and the Kinetics Dataset
    Joao Carreira, Andrew Zisserman
    https://arxiv.org/pdf/1705.07750v1.pdf.

  See also the Inception architecture, introduced in:

    Going deeper with convolutions
    Christian Szegedy, Wei Liu, Yangqing Jia, Pierre Sermanet, Scott Reed,
    Dragomir Anguelov, Dumitru Erhan, Vincent Vanhoucke, Andrew Rabinovich.
    http://arxiv.org/pdf/1409.4842v1.pdf.
  """

  # Endpoints of the model in order. During construction, all the endpoints up
  # to a designated `final_endpoint` are returned in a dictionary as the
  # second return value.
  VALID_ENDPOINTS = (
      'Conv3d_1a_7x7',
      'MaxPool3d_2a_3x3',
      'Conv3d_2b_1x1',
      'Conv3d_2c_3x3',
      'MaxPool3d_3a_3x3',
      'Mixed_3b',
      'Mixed_3c',
      'MaxPool3d_4a_3x3',
      'Mixed_4b',
      'Mixed_4c',
      'Mixed_4d',
      'Mixed_4e',
      'Mixed_4f',
      'MaxPool3d_5a_2x2',
      'Mixed_5b',
      'Mixed_5c',
      'Logits',
      'Predictions',
  )

  def __init__(self, num_classes=400, spatial_squeeze=True,
               final_endpoint='Logits', name='inception_i3d'):
    """Initializes I3D model instance.

    Args:
      num_classes: The number of outputs in the logit layer (default 400, which
          matches the Kinetics dataset).
      spatial_squeeze: Whether to squeeze the spatial dimensions for the logits
          before returning (default True).
      final_endpoint: The model contains many possible endpoints.
          `final_endpoint` specifies the last endpoint for the model to be built
          up to. In addition to the output at `final_endpoint`, all the outputs
          at endpoints up to `final_endpoint` will also be returned, in a
          dictionary. `final_endpoint` must be one of
          InceptionI3d.VALID_ENDPOINTS (default 'Logits').
      name: A string (optional). The name of this module.

    Raises:
      ValueError: if `final_endpoint` is not recognized.
    """

    if final_endpoint not in self.VALID_ENDPOINTS:
      raise ValueError('Unknown final endpoint %s' % final_endpoint)

    super(InceptionI3d, self).__init__(name=name)
    self._num_classes = num_classes
    self._spatial_squeeze = spatial_squeeze
    self._final_endpoint = final_endpoint

  def _build(self, inputs, is_training, dropout_keep_prob=1.0, channel_mult=1.0):
    """Connects the model to inputs.

    Args:
      inputs: Inputs to the model, which should have dimensions
          `batch_size` x `num_frames` x 224 x 224 x `num_channels`.
      is_training: whether to use training mode for snt.BatchNorm (boolean).
      dropout_keep_prob: Probability for the tf.nn.dropout layer (float in
          [0, 1)).

    Returns:
      A tuple consisting of:
        1. Network output at location `self._final_endpoint`.
        2. Dictionary containing all endpoints up to `self._final_endpoint`,
           indexed by endpoint name.

    Raises:
      ValueError: if `self._final_endpoint` is not recognized.
    """
    if self._final_endpoint not in self.VALID_ENDPOINTS:
      raise ValueError('Unknown final endpoint %s' % self._final_endpoint)

    net = inputs
    end_points = {}
    end_point = 'Conv3d_1a_7x7'
    net = Unit3D(output_channels=channel_mult*64, kernel_shape=[7, 7, 7],
                 stride=[2, 2, 2], name=end_point)(net, is_training=is_training)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'MaxPool3d_2a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 1, 3, 3, 1], strides=[1, 1, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'Conv3d_2b_1x1'
    net = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                 name=end_point)(net, is_training=is_training)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'Conv3d_2c_3x3'
    net = Unit3D(output_channels=channel_mult*192, kernel_shape=[3, 3, 3],
                 name=end_point)(net, is_training=is_training)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'MaxPool3d_3a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 1, 3, 3, 1], strides=[1, 1, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_3b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*96, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*16, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)

      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_3c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*192, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*96, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'MaxPool3d_4a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1], strides=[1, 2, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*192, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*96, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*208, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*16, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*48, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*112, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*224, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*24, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4d'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*256, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*24, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4e'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*112, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*144, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*288, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4f'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'MaxPool3d_5a_2x2'
    net = tf.nn.max_pool3d(net, ksize=[1, 2, 2, 2, 1], strides=[1, 2, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0a_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*384, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*192, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*384, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*48, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Logits'
    with tf.variable_scope(end_point):
      net = tf.nn.avg_pool3d(net, ksize=[1, 2, 7, 7, 1],
                             strides=[1, 1, 1, 1, 1], padding=snt.VALID)
      net = tf.nn.dropout(net, dropout_keep_prob)
      logits = Unit3D(output_channels=self._num_classes,
                      kernel_shape=[1, 1, 1],
                      activation_fn=None,
                      use_batch_norm=False,
                      use_bias=True,
                      name='Conv3d_0c_1x1')(net, is_training=is_training)
      if self._spatial_squeeze:
        logits = tf.squeeze(logits, [2, 3], name='SpatialSqueeze')
    averaged_logits = tf.reduce_mean(logits, axis=1)
    end_points[end_point] = averaged_logits
    if self._final_endpoint == end_point: return averaged_logits, end_points

    end_point = 'Predictions'
    predictions = tf.nn.softmax(averaged_logits)
    end_points[end_point] = predictions
    return predictions, end_points



###################################### tail ######################################

def Unit_custom_3D(output_channels,
                        kernel_shape=(1, 1, 1),
                        stride=(1, 1, 1),
                        activation_fn=tf.nn.relu,
                        use_batch_norm=True, # turn this false for nobn and use bias=true
                        use_bias=False,
                        name='unit_3d'):
    return Unit3D(output_channels,
                  kernel_shape,
                  stride,
                  activation_fn,
                  use_batch_norm,
                  use_bias,
                  name)

def i3d_tail(input_feats, is_training, final_endpoint, channel_mult=1.0):
    net = input_feats
    end_points = {}
    
    # end_point = 'Mixed_4f'
    # with tf.variable_scope(end_point):
    #   with tf.variable_scope('Branch_0'):
    #     branch_0 = Unit_custom_3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
    #                       name='Conv3d_0a_1x1')(net, is_training=is_training)
    #   with tf.variable_scope('Branch_1'):
    #     branch_1 = Unit_custom_3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
    #                       name='Conv3d_0a_1x1')(net, is_training=is_training)
    #     branch_1 = Unit_custom_3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
    #                       name='Conv3d_0b_3x3')(branch_1,
    #                                             is_training=is_training)
    #   with tf.variable_scope('Branch_2'):
    #     branch_2 = Unit_custom_3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
    #                       name='Conv3d_0a_1x1')(net, is_training=is_training)
    #     branch_2 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
    #                       name='Conv3d_0b_3x3')(branch_2,
    #                                             is_training=is_training)
    #   with tf.variable_scope('Branch_3'):
    #     branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
    #                                 strides=[1, 1, 1, 1, 1], padding=snt.SAME,
    #                                 name='MaxPool3d_0a_3x3')
    #     branch_3 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
    #                       name='Conv3d_0b_1x1')(branch_3,
    #                                             is_training=is_training)
    #   net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    # end_points[end_point] = net
    # # if self._final_endpoint == end_point: return net, end_points
    # if final_endpoint == end_point: return net, end_points

    end_point = 'MaxPool3d_5a_2x2'
    net = tf.nn.max_pool3d(net, ksize=[1, 2, 2, 2, 1], strides=[1, 2, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    # if self._final_endpoint == end_point: return net, end_points
    if final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit_custom_3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit_custom_3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit_custom_3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit_custom_3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0a_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    # if self._final_endpoint == end_point: return net, end_points
    if final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit_custom_3D(output_channels=channel_mult*384, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit_custom_3D(output_channels=channel_mult*192, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit_custom_3D(output_channels=channel_mult*384, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit_custom_3D(output_channels=channel_mult*48, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit_custom_3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    # if self._final_endpoint == end_point: return net, end_points
    if final_endpoint == end_point: return net, end_points
    return net, end_points
    # end_point = 'Logits'
    # with tf.variable_scope(end_point):
    #   net = tf.nn.avg_pool3d(net, ksize=[1, 2, 7, 7, 1],
    #                          strides=[1, 1, 1, 1, 1], padding=snt.VALID)
    #   net = tf.nn.dropout(net, dropout_keep_prob)
    #   logits = Unit3D(output_channels=self._num_classes,
    #                   kernel_shape=[1, 1, 1],
    #                   activation_fn=None,
    #                   use_batch_norm=False,
    #                   use_bias=True,
    #                   name='Conv3d_0c_1x1')(net, is_training=is_training)
    #   if self._spatial_squeeze:
    #     logits = tf.squeeze(logits, [2, 3], name='SpatialSqueeze')
    # averaged_logits = tf.reduce_mean(logits, axis=1)
    # end_points[end_point] = averaged_logits


############################ Lateral Connections for temporal context

def lateralconnection(inputs, t_span, output_channels, kernel_shape, stride, is_training, name):
    B, T, H, W, C = inputs.shape
    subsampling_step = T // t_span
    sub_in = inputs[:,::subsampling_step]

    #temporal_context = Unit3D(output_channels, kernel_shape, stride, name)(sub_in, is_training=is_training)
    with tf.variable_scope(name):
        temporal_context = snt.Conv3D(output_channels=output_channels,
                     kernel_shape=kernel_shape,
                     stride=stride,
                     padding=snt.SAME,
                     use_bias=True)(sub_in)
    #temporal_context = tf.layers.conv3d(sub_in, filters=output_channels, kernel_size=kernel_shape, strides=stride, padding='SAME', activation=tf.nn.relu, name=name)
    pooled_context = tf.nn.max_pool3d(temporal_context, ksize=[1, t_span, 1, 1, 1], strides=[1, t_span, 1, 1, 1], padding='VALID', name=name+'pooling')
    return pooled_context



class LateralInceptionI3d(snt.AbstractModule):
  """Inception-v1 I3D architecture.

  The model is introduced in:

    Quo Vadis, Action Recognition? A New Model and the Kinetics Dataset
    Joao Carreira, Andrew Zisserman
    https://arxiv.org/pdf/1705.07750v1.pdf.

  See also the Inception architecture, introduced in:

    Going deeper with convolutions
    Christian Szegedy, Wei Liu, Yangqing Jia, Pierre Sermanet, Scott Reed,
    Dragomir Anguelov, Dumitru Erhan, Vincent Vanhoucke, Andrew Rabinovich.
    http://arxiv.org/pdf/1409.4842v1.pdf.
  """

  # Endpoints of the model in order. During construction, all the endpoints up
  # to a designated `final_endpoint` are returned in a dictionary as the
  # second return value.
  VALID_ENDPOINTS = (
      'Conv3d_1a_7x7',
      'MaxPool3d_2a_3x3',
      'Conv3d_2b_1x1',
      'Conv3d_2c_3x3',
      'MaxPool3d_3a_3x3',
      'Mixed_3b',
      'Mixed_3c',
      'MaxPool3d_4a_3x3',
      'Mixed_4b',
      'Mixed_4c',
      'Mixed_4d',
      'Mixed_4e',
      'Mixed_4f',
      'MaxPool3d_5a_2x2',
      'Mixed_5b',
      'Mixed_5c',
      'Logits',
      'Predictions',
  )

  def __init__(self, num_classes=400, spatial_squeeze=True,
               final_endpoint='Logits', name='inception_i3d'):
    """Initializes I3D model instance.

    Args:
      num_classes: The number of outputs in the logit layer (default 400, which
          matches the Kinetics dataset).
      spatial_squeeze: Whether to squeeze the spatial dimensions for the logits
          before returning (default True).
      final_endpoint: The model contains many possible endpoints.
          `final_endpoint` specifies the last endpoint for the model to be built
          up to. In addition to the output at `final_endpoint`, all the outputs
          at endpoints up to `final_endpoint` will also be returned, in a
          dictionary. `final_endpoint` must be one of
          InceptionI3d.VALID_ENDPOINTS (default 'Logits').
      name: A string (optional). The name of this module.

    Raises:
      ValueError: if `final_endpoint` is not recognized.
    """

    if final_endpoint not in self.VALID_ENDPOINTS:
      raise ValueError('Unknown final endpoint %s' % final_endpoint)

    super(LateralInceptionI3d, self).__init__(name=name)
    self._num_classes = num_classes
    self._spatial_squeeze = spatial_squeeze
    self._final_endpoint = final_endpoint

  def _build(self, inputs, is_training, dropout_keep_prob=1.0, channel_mult=1.0):
    """Connects the model to inputs.

    Args:
      inputs: Inputs to the model, which should have dimensions
          `batch_size` x `num_frames` x 224 x 224 x `num_channels`.
      is_training: whether to use training mode for snt.BatchNorm (boolean).
      dropout_keep_prob: Probability for the tf.nn.dropout layer (float in
          [0, 1)).

    Returns:
      A tuple consisting of:
        1. Network output at location `self._final_endpoint`.
        2. Dictionary containing all endpoints up to `self._final_endpoint`,
           indexed by endpoint name.

    Raises:
      ValueError: if `self._final_endpoint` is not recognized.
    """
    if self._final_endpoint not in self.VALID_ENDPOINTS:
      raise ValueError('Unknown final endpoint %s' % self._final_endpoint)

    net = inputs
    end_points = {}
    end_point = 'Conv3d_1a_7x7'
    prev = net ###
    net = Unit3D(output_channels=channel_mult*64, kernel_shape=[7, 7, 7],
                 stride=[2, 2, 2], name=end_point)(net, is_training=is_training)
    lat = lateralconnection(prev, 4, 64, [1,7,7], [1,2,2], is_training, 'lateral1') ###
    net = net + lat ###
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'MaxPool3d_2a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 1, 3, 3, 1], strides=[1, 1, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'Conv3d_2b_1x1'
    prev = net ###
    net = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                 name=end_point)(net, is_training=is_training)
    lat = lateralconnection(prev, 4, 64, [1,1,1], [1,1,1], is_training, 'lateral2') ###
    net = net + lat ###
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'Conv3d_2c_3x3'
    prev = net ###
    net = Unit3D(output_channels=channel_mult*192, kernel_shape=[3, 3, 3],
                 name=end_point)(net, is_training=is_training)
    lat = lateralconnection(prev, 4, 192, [3,3,3], [1,1,1], is_training, 'lateral3') ###
    net = net + lat ###
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points
    end_point = 'MaxPool3d_3a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 1, 3, 3, 1], strides=[1, 1, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_3b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*96, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*16, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)

      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_3c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*192, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*96, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'MaxPool3d_4a_3x3'
    net = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1], strides=[1, 2, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*192, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*96, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*208, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*16, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*48, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*112, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*224, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*24, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4d'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*256, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*24, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4e'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*112, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*144, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*288, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*64, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*64, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_4f'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'MaxPool3d_5a_2x2'
    net = tf.nn.max_pool3d(net, ksize=[1, 2, 2, 2, 1], strides=[1, 2, 2, 2, 1],
                           padding=snt.SAME, name=end_point)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5b'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*256, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*160, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*320, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*32, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0a_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Mixed_5c'
    with tf.variable_scope(end_point):
      with tf.variable_scope('Branch_0'):
        branch_0 = Unit3D(output_channels=channel_mult*384, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
      with tf.variable_scope('Branch_1'):
        branch_1 = Unit3D(output_channels=channel_mult*192, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_1 = Unit3D(output_channels=channel_mult*384, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_1,
                                                is_training=is_training)
      with tf.variable_scope('Branch_2'):
        branch_2 = Unit3D(output_channels=channel_mult*48, kernel_shape=[1, 1, 1],
                          name='Conv3d_0a_1x1')(net, is_training=is_training)
        branch_2 = Unit3D(output_channels=channel_mult*128, kernel_shape=[3, 3, 3],
                          name='Conv3d_0b_3x3')(branch_2,
                                                is_training=is_training)
      with tf.variable_scope('Branch_3'):
        branch_3 = tf.nn.max_pool3d(net, ksize=[1, 3, 3, 3, 1],
                                    strides=[1, 1, 1, 1, 1], padding=snt.SAME,
                                    name='MaxPool3d_0a_3x3')
        branch_3 = Unit3D(output_channels=channel_mult*128, kernel_shape=[1, 1, 1],
                          name='Conv3d_0b_1x1')(branch_3,
                                                is_training=is_training)
      net = tf.concat([branch_0, branch_1, branch_2, branch_3], 4)
    end_points[end_point] = net
    if self._final_endpoint == end_point: return net, end_points

    end_point = 'Logits'
    with tf.variable_scope(end_point):
      net = tf.nn.avg_pool3d(net, ksize=[1, 2, 7, 7, 1],
                             strides=[1, 1, 1, 1, 1], padding=snt.VALID)
      net = tf.nn.dropout(net, dropout_keep_prob)
      logits = Unit3D(output_channels=self._num_classes,
                      kernel_shape=[1, 1, 1],
                      activation_fn=None,
                      use_batch_norm=False,
                      use_bias=True,
                      name='Conv3d_0c_1x1')(net, is_training=is_training)
      if self._spatial_squeeze:
        logits = tf.squeeze(logits, [2, 3], name='SpatialSqueeze')
    averaged_logits = tf.reduce_mean(logits, axis=1)
    end_points[end_point] = averaged_logits
    if self._final_endpoint == end_point: return averaged_logits, end_points

    end_point = 'Predictions'
    predictions = tf.nn.softmax(averaged_logits)
    end_points[end_point] = predictions
    return predictions, end_points
