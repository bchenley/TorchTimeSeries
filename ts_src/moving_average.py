import torch
import numpy as np

def moving_average(X, window):
    '''
    Applies a moving average filter to the input signal.

    Args:
        X: The input signal (PyTorch tensor).
        window: The window of the moving average filter.

    Returns:
        y: The output signal after applying the moving average filter.
    '''

    if not isinstance(X, torch.Tensor):
        X = torch.tensor(X)
    if not isinstance(window, torch.Tensor):
        window = torch.tensor(window).to(X)

    len_window = window.shape[0]

    y = torch.empty_like(X)

    ww = []

    for i in range(X.shape[0]):
        is_odd = int(np.mod(len_window, 2) == 1)

        m = torch.arange((i - (len_window - is_odd) / 2), (i + (len_window - is_odd) / 2 - (is_odd == 0) + 1),
                         dtype=torch.long)

        k = m[(m >= 0) & (m < X.shape[0])]

        window_ = window[(m >= 0) & (m < X.shape[0])]

        window_ /= window_.sum(0)

        y[i] = torch.matmul(window_, X[k])

    return y
