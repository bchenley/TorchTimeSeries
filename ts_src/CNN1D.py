import torch
import numpy as np

class CNN1D(torch.nn.Module):
    """
    CNN1D: A PyTorch module implementing a 1D Convolutional Neural Network.

    Args:
        in_channels (int): Number of input channels.
        out_channels (list): List of integers, specifying the number of output channels for each layer.
        kernel_size (list, optional): List of tuples specifying the kernel size for each layer. Default is [(1,)].
        kernel_stride (list, optional): List of tuples specifying the stride for each layer. Default is [(1,)].
        padding (list, optional): List of tuples specifying the padding for each layer. Default is [(0,)].
        dilation (list, optional): List of tuples specifying the dilation for each layer. Default is [(1,)].
        groups (list, optional): List of integers specifying the number of groups for each layer. Default is [1].
        bias (list, optional): List of booleans specifying whether to use bias for each layer. Default is [False].
        pool_type (list, optional): List of strings specifying the pooling type for each layer. 
                                    Options: [None, 'max', 'avg']. Default is [None].
        pool_size (list, optional): List of tuples specifying the pooling size for each layer. Default is [2].
        pool_stride (list, optional): List of tuples specifying the pooling stride for each layer. Default is [(1,)].
        batch_norm (bool, optional): If True, applies batch normalization after each convolutional layer. Default is False.
        batch_norm_learn (bool, optional): If True, allows the batch normalization layers to learn affine parameters. Default is False.
        pad_front (bool, optional): If True, pads the front of the input tensor with zeros before applying the convolutional layers. Default is False.
        device (str, optional): Device on which the model parameters should be stored. Default is None.
        dtype (torch.dtype, optional): Data type for the model parameters. Default is None.

    Methods:
        __init__(self, in_channels, out_channels, kernel_size, kernel_stride, padding, dilation, groups, bias, 
                  pool_type, pool_size, pool_stride, batch_norm, batch_norm_learn, pad_front, device, dtype):
            Constructor method for initializing the CNN1D module and its attributes.

        forward(self, input):
            Forward pass method that applies the 1D convolutional layers and pooling layers to the input tensor.

    Examples:
        # Create a CNN1D model with two layers, 32 input channels, and output channels [16, 8].
        cnn_model = CNN1D(in_channels=32, out_channels=[16, 8])

        # Apply the model to an input tensor X with shape [batch_size, input_channels, length].
        output = model(X)
    """
    def __init__(self, 
                 in_channels, out_channels, input_len=1,
                 pad_front=False,
                 kernel_size=[(1,)], kernel_stride=[(1,)], padding=[(0,)], 
                 dilation=[(1,)], groups=[1], bias=[False], 
                 pool_type=[None], pool_size=[(2,)], pool_stride=[(1,)],
                 batch_norm=False, batch_norm_learn=False,
                 device=None, dtype=None):
        """
        Constructor method for initializing the CNN1D module and its attributes.

        Args:
            in_channels (int): Number of input channels.
            out_channels (list): List of integers, specifying the number of output channels for each layer.
            kernel_size (list, optional): List of tuples specifying the kernel size for each layer. Default is [(1,)].
            kernel_stride (list, optional): List of tuples specifying the stride for each layer. Default is [(1,)].
            padding (list, optional): List of tuples specifying the padding for each layer. Default is [(0,)].
            dilation (list, optional): List of tuples specifying the dilation for each layer. Default is [(1,)].
            groups (list, optional): List of integers specifying the number of groups for each layer. Default is [1].
            bias (list, optional): List of booleans specifying whether to use bias for each layer. Default is [False].
            pool_type (list, optional): List of strings specifying the pooling type for each layer. 
                                        Options: [None, 'max', 'avg']. Default is [None].
            pool_size (list, optional): List of tuples specifying the pooling size for each layer. Default is [2].
            pool_stride (list, optional): List of tuples specifying the pooling stride for each layer. Default is [(1,)].
            batch_norm (bool, optional): If True, applies batch normalization after each convolutional layer. Default is False.
            batch_norm_learn (bool, optional): If True, allows the batch normalization layers to learn affine parameters. Default is False.
            pad_front (bool, optional): If True, pads the front of the input tensor with zeros before applying the convolutional layers. Default is False.
            device (str, optional): Device on which the model parameters should be stored. Default is None.
            dtype (torch.dtype, optional): Data type for the model parameters. Default is None.
        """
        super(CNN1D, self).__init__()

        # Store the arguments as attributes
        locals_ = locals().copy()
        for arg in locals_:
            if arg != 'self':
                setattr(self, arg, locals_[arg])

        self.num_layers = len(out_channels)

        # Replicate single-value parameters to match the number of layers
        if len(self.kernel_size) == 1: self.kernel_size = self.kernel_size * self.num_layers
        if len(self.kernel_stride) == 1: self.kernel_stride = self.kernel_stride * self.num_layers
        if len(self.padding) == 1: self.padding = self.padding * self.num_layers
        if len(self.dilation) == 1: self.dilation = self.dilation * self.num_layers
        if len(self.groups) == 1: self.groups = self.groups * self.num_layers
        if len(self.bias) == 1: self.bias = self.bias * self.num_layers
        if len(self.pool_type) == 1: self.pool_type = self.pool_type * self.num_layers
        if len(self.pool_size) == 1: self.pool_size = self.pool_size * self.num_layers
        if len(self.pool_stride) == 1: self.pool_stride = self.pool_stride * self.num_layers

        # Create the CNN layers
        self.cnn = torch.nn.ModuleList()  
        for i in range(self.num_layers):
            self.cnn.append(torch.nn.Sequential())

            # Determine the input channels for the current layer
            in_channels_i = self.in_channels if i == 0 else self.out_channels[i - 1]

            # Add Conv1d layer to the current layer
            self.cnn[-1].append(torch.nn.Conv1d(in_channels=in_channels_i,
                                                out_channels=self.out_channels[i],
                                                kernel_size=self.kernel_size[i],
                                                stride=self.kernel_stride[i],
                                                padding=self.padding[i],
                                                dilation=self.dilation[i],
                                                groups=self.groups[i],
                                                bias=self.bias[i],
                                                device=self.device, dtype=self.dtype))

            # Add batch normalization layer if specified
            if self.batch_norm:
                batch_norm_i = torch.nn.BatchNorm1d(self.out_channels[i], 
                                                    affine=self.batch_norm_learn,
                                                    device = self.device, dtype = self.dtype)
            else:
                batch_norm_i = torch.nn.Identity()

            self.cnn[-1].append(batch_norm_i)

            # Add pooling layer if specified
            if self.pool_type[i] is None:
                pool_i = torch.nn.Identity()
            if self.pool_type[i] == 'max':
                pool_i = torch.nn.MaxPool1d(kernel_size=self.pool_size[i],
                                            stride=self.pool_stride[i])
            elif self.pool_type[i] == 'avg':
                pool_i = torch.nn.AvgPool1d(kernel_size=self.pool_size[i],
                                            stride=self.pool_stride[i])     

            self.cnn[-1].append(pool_i)
        
        # Determine the number of output features after passing through the layers
        with torch.no_grad():             
            X = torch.zeros((1, self.input_len, in_channels)).to(device=self.device, dtype=self.dtype)
            self.out_features = self.forward(X).shape[-1]

    def forward(self, input):
        """ 
        Forward pass method that applies the 1D convolutional layers and pooling layers to the input tensor.

        Args:
            input (torch.Tensor): Input tensor with shape [batch_size, input_channels, length].

        Returns:
            torch.Tensor: Output tensor after passing through the CNN1D module.
        """
        output = input.clone()
        for i in range(self.num_layers):   
            # Apply padding to the input tensor if pad_front is True
            input_i = torch.nn.functional.pad(output.transpose(1, 2), (self.kernel_size[i][0] - 1, 0)) if self.pad_front else output.transpose(1, 2)
            # Apply the current CNN layer to the input tensor
            output = self.cnn[i][1](self.cnn[i][0](input_i))
            # Transpose back the output tensor to the original shape
            output = self.cnn[i][2](output).transpose(1, 2)

        return output
