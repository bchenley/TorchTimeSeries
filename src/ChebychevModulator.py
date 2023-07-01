import torch

class ChebychevModulator(torch.nn.Module):
  '''
  Chebychev modulation function.

  Args:
      window_len (int): Length of the input window.
      scale (bool): If True, scale the input to the range [0, 1].
      kind (int): Kind of Chebychev polynomial to use. Must be 1 or 2.
      degree (int): Degree of the Chebychev polynomial.
      zero_order (bool): If True, include the zeroth-order term (constant) in the modulation function.
      device (str): Device to use for computation ('cpu' or 'cuda').
      dtype (torch.dtype): Data type of the model parameters.

  '''

  def __init__(self, window_len, scale=True, kind=1, degree=1, zero_order=True, device='cpu', dtype=torch.float32):
    super(ChebychevModulator, self).__init__()

    self.degree = degree
    self.zero_order = zero_order
    self.num_modulators = degree + int(zero_order)

    self.device, self.dtype = device, dtype

    self.window_len = window_len
    self.scale = scale
    self.kind = kind
    self.functions = self.generate_basis_functions()

  def generate_basis_functions(self):
    '''
    Generate the Chebychev basis functions.

    Returns:
        torch.Tensor: Chebychev basis functions.

    '''
    t = torch.arange(0, self.window_len).view(-1, 1).to(device=self.device, dtype=self.dtype)
    t = t / (t.max() - t.min()) if self.scale else t

    N = len(t)

    y = torch.zeros((N, (self.degree + 1))).to(device=self.device, dtype=self.dtype)

    for q in range(0, (self.degree + 1)):
        if q == 0:
            y[:, 0] = torch.ones((N,)).to(device=self.device, dtype=self.dtype)
        elif q == 1:
            y[:, 1:2] = self.kind * t * y[:, 0:1]
        else:
            y[:, q:(q + 1)] = 2 * t * y[:, (q - 1):q] - y[:, (q - 2):(q - 1)]

    if not self.zero_order:
        y = y[:, 1:]

    self.functions = y

    return y

  def forward(self, X, steps):
    '''
    Apply the Chebychev modulation to the input.

    Args:
        X (torch.Tensor): Input tensor.
        steps (int): Index of the modulation step.

    Returns:
        torch.Tensor: Modulated tensor.

    '''
    X = X.to(device=self.device, dtype=self.dtype)

    y = X[:, :, None, :] * self.functions[steps]

    return y

