import torch

def clear_cuda_tensors(glbs = globals(), lcls = locals()):
  vars_to_del = []
  for name, var in glbs.items():
    if isinstance(var, torch.Tensor):
       if var.device == 'cuda':
         vars_to_del.append(name)
    elif isinstance(var, list):
      if all(isinstance(var_, torch.Tensor) for var_ in var):
        if var.device == 'cuda':
          vars_to_del.append(name)

  for name in vars_to_del:
        del glbs[name]
        glbs[name] = None
  
  vars_to_del = []
  for name, var in lcls.items():
    if isinstance(var, torch.Tensor):
       if var.device == 'cuda':
         vars_to_del.append(name)
    elif isinstance(var, list):
      if all(isinstance(var_, torch.Tensor) for var_ in var):
        if var.device == 'cuda':
          vars_to_del.append(name)

  for name in vars_to_del:
        del lcls[name]
        lcls[name] = None
