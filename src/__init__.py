print("Initializing TorchTimeSeries package...")

import importlib, pkgutil

# __all__ = ['FeatureTransform',
#            'Loss', 
#            'fft', 
#            'periodogram', 
#            'moving_average', 
#            'butter', 
#            'fill', 
#            'Interpolator', 
#            'remove_outliers', 
#            'BaselineModel', 
#            'Polynomial', 
#            'LRU', 
#            'HiddenLayer', 
#            'ModulationLayer',
#            'LegendreModulator',
#            'ChebychevModulator', 
#            'FourierModulator', 
#            'SigmoidModulator', 
#            'Attention', 
#            'TransformerEncoderLayer',
#            'TransformerDecoderLayer', 
#            'SequenceModelBase', 
#            'SequenceModel', 
#            'Seq2SeqModel', 
#            'Embedding', 
#            'PositionalEncoding', 
#            'SequenceDataset',                        
#            'SequenceDataloader',
#            'DataModule',
#            'SequenceModule']

# for module_name in __all__:
#     module = importlib.import_module(f'.{module_name}', __name__)
#     globals()[module_name] = getattr(module, module_name)

__all__ = [ ]
for module_info in pkgutil.iter_modules(['src']):
    module_name = module_info.name
    __all__.append(module_name)
    module = importlib.import_module(f'src.{module_name}')
    globals()[module_name] = module
    
print("Done")
