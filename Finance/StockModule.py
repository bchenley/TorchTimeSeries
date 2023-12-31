import pytorch_lightning as pl
import torch

import numpy as np
import pandas as pd

import time

from tqdm.auto import tqdm
import matplotlib.pyplot as plt

from TorchTimeSeries.ts_src.Criterion import Criterion

class StockModule(pl.LightningModule):
  def __init__(self,
               model,
               opt, loss_fn, metric_fn = None,
               constrain = False, penalize = False,
               track_performance = False, track_params = False,
               model_dir = None):

    super().__init__()

    self.automatic_optimization = False

    self.model = model

    self.opt, self.loss_fn, self.metric_fn = opt, loss_fn, metric_fn

    self.constrain, self.penalize = constrain, penalize

    input_size, output_size = self.model.input_size, self.model.output_size

    self.train_history, self.val_history = None, None
    self.current_val_epoch = 0

    self.train_step_loss, self.train_step_metric = [], []
    self.val_epoch_loss, self.val_epoch_metric = [], []
    self.test_epoch_loss, self.test_epoch_metric = [], []

    self.hiddens = None

    self.track_performance, self.track_params = track_performance, track_params

    self.model_dir = model_dir

  def forward(self,
              input,
              hiddens = None,
              steps = None,
              target = None,
              input_window_idx = None, output_window_idx = None,
              output_mask = None,
              output_input_idx = None, input_output_idx = None,
              encoder_output= None):

    output, hiddens = self.model.forward(input = input,
                                         steps = steps,
                                         hiddens = hiddens,
                                         target = target,
                                         input_window_idx = input_window_idx,
                                         output_window_idx = output_window_idx,
                                         output_mask = output_mask,
                                         output_input_idx = output_input_idx,
                                         input_output_idx = input_output_idx,
                                         encoder_output= encoder_output)

    return output, hiddens

  ## Configure optimizers
  def configure_optimizers(self):
    return self.opt
  ##

  ## train model
  def on_train_start(self):
    self.run_time = time.time()

  def training_step(self, batch, batch_idx):

    # constrain model if desired
    if self.constrain: self.model.constrain()
    #

    # unpack batch
    input_batch, output_batch, steps_batch, batch_size = batch
    #

    # keep the first `batch_size` batches of hiddens
    if self.hiddens is not None:
      for i in range(self.model.num_inputs):
        if (self.model.base_type[i] in ['gru', 'lstm', 'lru']) & (self.hiddens[i] is not None):
          if self.model.base_type[i] == 'lstm':
            if self.hiddens[i][0].shape[1] >= batch_size:
              self.hiddens[i] = [s[:, :batch_size].contiguous() for s in self.hiddens[i]]
            else:
              self.hiddens[i] = [torch.nn.functional.pad(s.contiguous(), pad=(0, 0, 0, batch_size-s.shape[1]), mode='constant', value=0) for s in self.hiddens[i]]
          else:
            if self.hiddens[i].shape[1] >= batch_size:
              self.hiddens[i] = self.hiddens[i][:, :batch_size].contiguous()
            else:
              self.hiddens[i] = torch.nn.functional.pad(self.hiddens[i].contiguous(), pad=(0, 0, 0, batch_size-self.hiddens[i].shape[1]), mode='constant', value=0)

    input_batch = input_batch[:batch_size]
    output_batch = output_batch[:batch_size]
    steps_batch = steps_batch[:batch_size]
    #

    # perform forward pass to compute gradients
    output_pred_batch, self.hiddens = self.forward(input = input_batch,
                                                   steps = steps_batch,
                                                   hiddens = self.hiddens,
                                                   target = output_batch,
                                                   input_window_idx = self.trainer.datamodule.train_input_window_idx,
                                                   output_window_idx = self.trainer.datamodule.train_output_window_idx,
                                                   output_input_idx = self.trainer.datamodule.output_input_idx,
                                                   input_output_idx = self.trainer.datamodule.input_output_idx,
                                                   output_mask = self.trainer.datamodule.train_output_mask)
    #

    # get loss for each output
    loss = self.loss_fn(output_pred_batch*self.trainer.datamodule.train_output_mask,
                        output_batch*self.trainer.datamodule.train_output_mask)

    loss = torch.stack([l.sum() for l in loss.split(self.model.output_size, -1)], 0)
    #

    # add penalty loss if desired
    if self.penalize: loss += self.model.penalize()
    #

    self.opt.zero_grad()
    if len(loss) > 1:
      for i in range(len(loss)):
        loss[i].backward(retain_graph = True)
    else:
      loss.backward()
    self.opt.step()

    # store loss to be used later in `on_train_epoch_end`
    self.train_step_loss.append(loss)    
    #

    metric = None
    if self.metric_fn is not None:
      # get metric for each output    
      metric = self.metric_fn(output_pred_batch*self.trainer.datamodule.train_output_mask,
                              output_batch*self.trainer.datamodule.train_output_mask)

      metric = torch.stack([m.sum() for m in metric.split(self.model.output_size, -1)], 0)
      self.train_step_metric.append(metric)
      #

    return {"loss": loss, "metric": metric}

  def on_train_batch_start(self, batch, batch_idx):
    if self.hiddens is not None:
      for i in range(self.model.num_inputs):
        if (self.model.base_type[i] in ['gru', 'lstm', 'lru']) & (self.hiddens[i] is not None):
          if self.model.base_type[i] == 'lstm':
            self.hiddens[i] = [s.detach() for s in self.hiddens[i]]
          else:
            self.hiddens[i] = self.hiddens[i].detach()

  def on_train_batch_end(self, outputs, batch, batch_idx):

    # reduced loss of current batch
    train_step_loss = outputs['loss'].detach()
    train_step_metric = outputs['metric'].detach() if outputs['metric'] is not None else None
    #

    # log and display sum of batch loss
    self.log('train_step_loss', train_step_loss.sum(), on_step = True, prog_bar = True)
    #

    if self.track_performance or self.track_params:
      if self.train_history is None:
        self.current_train_step = 0
        self.train_history = {'steps': torch.empty((0, 1)).to(device = train_step_loss.device,
                                                              dtype = torch.long)}
        if self.track_performance:
          for i in range(self.model.num_outputs):
            loss_name_i = self.loss_fn.name + '_' + self.trainer.datamodule.output_names[i]
            self.train_history[loss_name_i] = torch.empty((0, 1)).to(train_step_loss)

            if train_step_metric is not None:
              metric_name_i = self.metric_fn.name + '_' + self.trainer.datamodule.output_names[i]
              self.train_history[metric_name_i] = torch.empty((0, 1)).to(train_step_metric)

        if self.track_params:
          for name, param in self.model.named_parameters():
            if param.requires_grad == True:
              self.train_history[name] = torch.empty((0, param.numel())).to(param)

      else:
        self.train_history['steps'] = torch.cat((self.train_history['steps'],
                                                 torch.tensor(self.current_train_step).reshape(1, 1).to(train_step_loss)), 0)

        if self.track_performance:
          for i in range(self.trainer.datamodule.num_outputs):
            loss_name_i = self.loss_fn.name + '_' + self.trainer.datamodule.output_names[i]
            self.train_history[loss_name_i] = torch.cat((self.train_history[loss_name_i],
                                                       train_step_loss[i].cpu().reshape(1, 1).to(train_step_loss)), 0)

            if train_step_metric is not None:
              metric_name_i = self.metric_fn.name + '_' + self.trainer.datamodule.output_names[i]
            self.train_history[metric_name_i] = torch.cat((self.train_history[metric_name_i],
                                                           train_step_metric[i].cpu().reshape(1, 1).to(train_step_metric)), 0)
            
        if self.track_params:
          for i,(name, param) in enumerate(self.model.named_parameters()):
            if param.requires_grad:
              self.train_history[name] = torch.cat((self.train_history[name],
                                                    param.detach().cpu().reshape(1, -1).to(param)), 0)

      self.current_train_step += 1

  def on_train_epoch_start(self):
    self.hiddens = None
    self.train_step_loss = []

  def on_train_epoch_end(self):

    # epoch loss
    train_epoch_loss = torch.stack(self.train_step_loss).mean(0)
    #

    self.log('train_epoch_loss', train_epoch_loss.sum(), on_epoch = True, prog_bar = True)

    self.train_step_loss.clear()
  ## End of Training

  ## Validate Model
  def validation_step(self, batch, batch_idx):

    # unpack batch
    input_batch, output_batch, steps_batch, batch_size = batch
    #

    # keep the first `batch_size` batches of hiddens
    if self.hiddens is not None:
      
      for i in range(self.model.num_inputs):
        if (self.model.base_type[i] in ['gru', 'lstm', 'lru']) & (self.hiddens[i] is not None):
          if self.model.base_type[i] == 'lstm':
            if self.hiddens[i][0].shape[1] >= batch_size:
              self.hiddens[i] = [s[:, :batch_size].contiguous() for s in self.hiddens[i]]
            else:
              self.hiddens[i] = [torch.nn.functional.pad(s.contiguous(), pad=(0, 0, 0, batch_size-s.shape[1]), mode='constant', value=0) for s in self.hiddens[i]]
          else:
            if self.hiddens[i].shape[1] >= batch_size:
              self.hiddens[i] = self.hiddens[i][:, :batch_size].contiguous()
            else:
              self.hiddens[i] = torch.nn.functional.pad(self.hiddens[i].contiguous(), pad=(0, 0, 0, batch_size-self.hiddens[i].shape[1]), mode='constant', value=0)

    input_batch = input_batch[:batch_size]
    output_batch = output_batch[:batch_size]
    steps_batch = steps_batch[:batch_size]
    #

    # perform forward pass to compute gradients
    output_pred_batch, self.hiddens = self.forward(input = input_batch,
                                                  steps = steps_batch,
                                                  hiddens = self.hiddens,
                                                  target = None,
                                                  input_window_idx = self.trainer.datamodule.val_input_window_idx, 
                                                  output_window_idx = self.trainer.datamodule.val_output_window_idx,
                                                  output_input_idx = self.trainer.datamodule.output_input_idx,
                                                  input_output_idx = self.trainer.datamodule.input_output_idx,
                                                  output_mask = self.trainer.datamodule.val_output_mask)
    #

    # get loss for each output
    loss = self.loss_fn(output_pred_batch*self.trainer.datamodule.val_output_mask,
                        output_batch*self.trainer.datamodule.val_output_mask)
    loss = torch.stack([l.sum() for l in loss.split(self.model.output_size, -1)], 0)
    #

    self.val_epoch_loss.append(loss)

    metric = None
    if self.metric_fn is not None:
      # get metric for each output    
      metric = self.metric_fn(output_pred_batch*self.trainer.datamodule.val_output_mask,
                              output_batch*self.trainer.datamodule.val_output_mask)

      metric = torch.stack([m.sum() for m in metric.split(self.model.output_size, -1)], 0)
      self.val_epoch_metric.append(metric)
      #

    return {"loss": loss, "metric": metric}

  def on_validation_epoch_end(self):
    # epoch loss
    val_epoch_loss = torch.stack(self.val_epoch_loss).mean(0)
    val_epoch_metric = torch.stack(self.val_epoch_metric).mean(0) if len(self.val_epoch_metric) > 0 else None
    #

    self.log('val_epoch_loss', val_epoch_loss.sum(), on_step = False, on_epoch = True, prog_bar = True)

    if self.track_performance:
      if self.val_history is None:
        self.val_history = {'epochs': torch.empty((0, 1)).to(device = val_epoch_loss.device,
                                                             dtype = torch.long)}
        for i in range(self.trainer.datamodule.num_outputs):
          self.val_history[self.loss_fn.name + '_' + self.trainer.datamodule.output_names[i]] = torch.empty((0, 1)).to(val_epoch_loss)

          if val_epoch_metric is not None:
            metric_name_i = self.metric_fn.name + '_' + self.trainer.datamodule.output_names[i]
            self.val_history[metric_name_i] = torch.empty((0, 1)).to(val_epoch_metric)

      else:
        self.val_history['epochs'] = torch.cat((self.val_history['epochs'],
                                              torch.tensor(self.current_val_epoch).reshape(1, 1).to(val_epoch_loss)), 0)

        for i in range(self.trainer.datamodule.num_outputs):
          loss_name_i = self.loss_fn.name + '_' + self.trainer.datamodule.output_names[i]
          self.val_history[loss_name_i] = torch.cat((self.val_history[loss_name_i],
                                                    val_epoch_loss[i].cpu().reshape(1, 1).to(val_epoch_loss)), 0)
          
          if val_epoch_metric is not None:
            metric_name_i = self.metric_fn.name + '_' + self.trainer.datamodule.output_names[i]
          self.val_history[metric_name_i] = torch.cat((self.val_history[metric_name_i],
                                                       val_epoch_metric[i].cpu().reshape(1, 1).to(val_epoch_metric)), 0)

      self.current_val_epoch += 1

    self.val_epoch_loss.clear()
    self.val_epoch_metric.clear()

  ## End of validation

  ## Test Model
  def test_step(self, batch, batch_idx):

    # unpack batch
    input_batch, output_batch, steps_batch, batch_size = batch
    #

    # keep the first `batch_size` batches of hiddens
    if self.hiddens is not None:
      for i in range(self.model.num_inputs):
        if (self.model.base_type[i] in ['gru', 'lstm', 'lru']) & (self.hiddens[i] is not None):
          if self.model.base_type[i] == 'lstm':
            if self.hiddens[i][0].shape[1] >= batch_size:
              self.hiddens[i] = [s[:, :batch_size].contiguous() for s in self.hiddens[i]]
            else:
              self.hiddens[i] = [torch.nn.functional.pad(s.contiguous(), pad=(0, 0, 0, batch_size-s.shape[1]), mode='constant', value=0) for s in self.hiddens[i]]
          else:
            if self.hiddens[i].shape[1] >= batch_size:
              self.hiddens[i] = self.hiddens[i][:, :batch_size].contiguous()
            else:
              self.hiddens[i] = torch.nn.functional.pad(self.hiddens[i].contiguous(), pad=(0, 0, 0, batch_size-self.hiddens[i].shape[1]), mode='constant', value=0)

    input_batch = input_batch[:batch_size]
    output_batch = output_batch[:batch_size]
    steps_batch = steps_batch[:batch_size]
    #

    # perform forward pass to compute gradients
    output_pred_batch, self.hiddens = self.forward(input = input_batch,
                                                  steps = steps_batch,
                                                  hiddens = self.hiddens,
                                                  target = None,
                                                  input_window_idx = self.trainer.datamodule.test_input_window_idx, 
                                                  output_window_idx = self.trainer.datamodule.test_output_window_idx,
                                                  output_input_idx = self.trainer.datamodule.output_input_idx,
                                                  input_output_idx = self.trainer.datamodule.input_output_idx,
                                                  output_mask = self.trainer.datamodule.test_output_mask)
    #

    # get loss for each output
    loss = self.loss_fn(output_pred_batch*self.trainer.datamodule.test_output_mask,
                        output_batch*self.trainer.datamodule.test_output_mask)
    loss = torch.stack([l.sum() for l in loss.split(self.model.output_size, -1)], 0)
    #

    self.test_epoch_loss.append(loss)

    metric = None
    if self.metric_fn is not None:
      # get metric for each output    
      metric = self.metric_fn(output_pred_batch*self.trainer.datamodule.test_output_mask,
                              output_batch*self.trainer.datamodule.test_output_mask)

      metric = torch.stack([m.sum() for m in metric.split(self.model.output_size, -1)], 0)
      self.test_epoch_metric.append(metric)
      #

    return {"loss": loss, "metric": metric}

  def on_test_epoch_end(self):
    # epoch loss
    test_epoch_loss = torch.stack(self.test_epoch_loss).mean(0)
    test_epoch_metric = torch.stack(self.test_epoch_metric).mean(0) if len(self.test_epoch_metric) > 0 else None

    self.test_epoch_loss.clear()
    self.test_epoch_metric.clear()
    #

    self.log('test_epoch_loss', test_epoch_loss.sum(), on_epoch = True, prog_bar = True)
  ## End of Testing

  ## plot history
  def plot_history(self, history = None, plot_train_history_by = 'epochs', figsize = None):

    history = [self.loss_fn.name] if history is None else history

    if plot_train_history_by == 'epochs':
      num_batches = len(self.trainer.datamodule.train_dl.dl)
      train_history_epoch = {'epochs': torch.arange(len(self.train_history['steps'])//num_batches).to(dtype = torch.long)}
      num_epochs = len(train_history_epoch['epochs'])
      for key in self.train_history.keys():
        if key != 'steps':
          batch_param = []
          for batch in self.train_history[key].split(num_batches, 0):
            batch_param.append(batch.mean(0, keepdim = True))
          batch_param = torch.cat(batch_param, 0)
          train_history_epoch[key] = batch_param[:num_epochs]

      train_history = train_history_epoch

      x_label = 'epochs'

    else:
      x_label = 'steps'
      train_history = self.train_history

    num_params = len(history)
    fig = plt.figure(figsize = figsize if figsize is not None else (5, 5*num_params))
    ax_i = 0
    for param in history:
      ax_i += 1
      ax = fig.add_subplot(num_params, 1, ax_i)
      ax.plot(train_history[x_label].cpu(), train_history[param].cpu(), 'k', label = 'Train')
      if (self.val_history is not None) & (param in self.val_history) & (x_label == 'epochs'):
        N = np.min([self.val_history[x_label].shape[0], self.val_history[param].shape[0]])

        if self.loss_fn.name in param:
          metric = self.val_history[param][:N]
        elif self.metric_fn.name is not None:
          if self.metric_fn.name in param:
            metric = self.val_history[param][:N]

        ax.plot(self.val_history[x_label][:N].cpu(), metric.cpu(), 'r', label = 'Val')
      ax.set_title(param)
      ax.set_xlabel(x_label)
      ax.set_ylabel(param)
      ax.grid()
      ax.legend()
      
  ##

  ## Prediction
  def predict_step(self, batch, batch_idx):

    # unpack batch
    input_batch, output_batch, steps_batch, batch_size = batch
    #

    # keep the first `batch_size` batches of hiddens
    if self.hiddens is not None:
      for i in range(self.model.num_inputs):
        if (self.model.base_type[i] in ['gru', 'lstm', 'lru']) & (self.hiddens[i] is not None):
          if self.model.base_type[i] == 'lstm':
            if self.hiddens[i][0].shape[1] >= batch_size:
              self.hiddens[i] = [s[:, :batch_size].contiguous() for s in self.hiddens[i]]
            else:
              self.hiddens[i] = [torch.nn.functional.pad(s.contiguous(), pad=(0, 0, 0, batch_size-s.shape[1]), mode='constant', value=0) for s in self.hiddens[i]]
          else:
            if self.hiddens[i].shape[1] >= batch_size:
              self.hiddens[i] = self.hiddens[i][:, :batch_size].contiguous()
            else:
              self.hiddens[i] = torch.nn.functional.pad(self.hiddens[i].contiguous(), pad=(0, 0, 0, batch_size-self.hiddens[i].shape[1]), mode='constant', value=0)

    input_batch = input_batch[:batch_size]
    output_batch = output_batch[:batch_size]
    steps_batch = steps_batch[:batch_size]
    #

    output_len = output_batch.shape[1]

    # perform forward pass to compute gradients
    output_pred_batch, self.hiddens = self.forward(input = input_batch,
                                                   steps = steps_batch,
                                                   hiddens = self.hiddens,
                                                   target = None,
                                                   input_window_idx = self.predict_input_window_idx,
                                                   output_window_idx = self.predict_output_window_idx,
                                                   output_input_idx = self.trainer.datamodule.output_input_idx,
                                                   input_output_idx = self.trainer.datamodule.input_output_idx,
                                                   output_mask = self.predict_output_mask)
    #

    # get loss for each output
    step_loss = self.loss_fn(output_pred_batch*self.predict_output_mask,
                             output_batch*self.predict_output_mask)
    step_loss = torch.stack([l.sum() for l in step_loss.split(self.model.output_size, -1)], 0)
    #

    output_steps_batch = steps_batch[:, -output_len:]

    return output_batch, output_pred_batch, output_steps_batch # , baseline_pred_batch

  def on_predict_batch_end(self, outputs, batch, batch_idx, dataloader_idx = 0):
    self.step_target.append(outputs[0])
    self.output_pred_batch.append(outputs[1])
    self.output_steps_batch.append(outputs[2])
    # self.step_baseline_pred.append(outputs[3])

  def on_predict_epoch_end(self):
    self.target = torch.cat(self.step_target, 0)
    self.prediction = torch.cat(self.output_pred_batch, 0)
    self.output_steps = torch.cat(self.output_steps_batch, 0)
    # self.baseline_prediction = torch.cat(self.step_baseline_pred, 0)

    self.step_target.clear()
    self.output_pred_batch.clear()
    self.output_steps_batch.clear()
    # self.step_baseline_pred.clear()

  def on_predict_epoch_start(self):
    self.output_pred_batch, self.step_target = [], []
    self.output_steps_batch = []
    # self.step_baseline_pred = []

  def predict(self,
              reduction = 'mean',
              baseline_model = None):
  
    self.model.to(device = self.trainer.datamodule.device,
                  dtype = self.trainer.datamodule.dtype)

    self.baseline_model = baseline_model
    
    self.trainer.datamodule.predicting = True

    self.trainer.enable_progress_bar = False
    
    start_step = self.trainer.datamodule.start_step if self.trainer.datamodule.pad_data else 0
    
    time_name = self.trainer.datamodule.time_name

    self.hiddens = None
    
    with torch.no_grad():

      ## Predict training data      
      self.predict_output_mask = self.trainer.datamodule.train_output_mask
      self.predict_input_window_idx = self.trainer.datamodule.train_input_window_idx
      self.predict_output_window_idx = self.trainer.datamodule.train_output_window_idx
      
      self.trainer.predict(self, self.trainer.datamodule.train_dl.dl)

      train_prediction, train_output_steps = self.generate_reduced_output(self.prediction, self.output_steps,
                                                                          reduction = reduction, transforms=self.trainer.datamodule.transforms)

      train_target, _ = self.generate_reduced_output(self.target, self.output_steps,
                                                     reduction = reduction, transforms=self.trainer.datamodule.transforms)

      train_time =  self.trainer.datamodule.data[self.time_name][(train_output_steps.cpu() - start_step).numpy()]

      train_baseline_pred, train_baseline_loss = None, None
      if self.baseline_model is not None:
        train_baseline_pred = self.baseline_model(train_target)
        # train_baseline_loss = self.loss_fn(train_baseline_pred.unsqueeze(0),
        #                                    train_target.unsqueeze(0))
      ##
     
      # Predict validation data
      val_prediction, val_target, val_time, val_loss, val_baseline_pred, val_baseline_loss = None, None, None, None, None, None
      if len(self.trainer.datamodule.val_dl.dl) > 0:
        self.predict_output_mask = self.trainer.datamodule.val_output_mask
        self.predict_input_window_idx = self.trainer.datamodule.val_input_window_idx
        self.predict_output_window_idx = self.trainer.datamodule.val_output_window_idx

        self.trainer.predict(self, self.trainer.datamodule.val_dl.dl) ;

        val_prediction, val_output_steps = self.generate_reduced_output(self.prediction, self.output_steps,
                                                                        reduction = reduction, transforms=self.trainer.datamodule.transforms)

        val_target, _ = self.generate_reduced_output(self.target, self.output_steps,
                                                     reduction = reduction, transforms=self.trainer.datamodule.transforms)

        val_time =  self.trainer.datamodule.data[self.time_name][(val_output_steps.cpu() - start_step).numpy()]

        if not self.trainer.datamodule.pad_data:
          val_time = val_time[start_step:]

        val_baseline_pred, val_baseline_loss = None, None
        if self.baseline_model is not None:
          val_baseline_pred = self.baseline_model(val_target)          
      #

      # Predict testing data
      if not hasattr(self.trainer.datamodule, 'test_dl'):
        self.trainer.datamodule.test_dataloader()
      test_prediction, test_target, test_time, test_loss, test_baseline_pred, test_baseline_loss = None, None, None, None, None, None
      if len(self.trainer.datamodule.test_dl.dl) > 0:
        self.predict_output_mask = self.trainer.datamodule.test_output_mask
        self.predict_input_window_idx = self.trainer.datamodule.test_input_window_idx
        self.predict_output_window_idx = self.trainer.datamodule.test_output_window_idx

        self.trainer.predict(self, self.trainer.datamodule.test_dl.dl) ;

        test_prediction, test_output_steps = self.generate_reduced_output(self.prediction, self.output_steps,
                                                                          reduction = reduction, transforms = self.trainer.datamodule.transforms)

        test_target, _ = self.generate_reduced_output(self.target, self.output_steps,
                                                      reduction = reduction, transforms=self.trainer.datamodule.transforms)

        test_time =  self.trainer.datamodule.data[self.time_name][(test_output_steps.cpu() - start_step).numpy()]

        if not self.trainer.datamodule.pad_data:
          test_time = test_time[start_step:]

        test_baseline_pred, test_baseline_loss = None, None
        if self.baseline_model is not None:
          test_baseline_pred = self.baseline_model(test_target)          
      #

    train_prediction_data, val_prediction_data, test_prediction_data = {time_name: train_time}, None, None

    train_prediction_data['steps'] = train_output_steps

    if val_prediction is not None:
      val_prediction_data = {time_name: val_time}
      val_prediction_data['steps'] = val_output_steps

    if test_prediction is not None:
      test_prediction_data = {time_name: test_time}
      test_prediction_data['steps'] = test_output_steps

    for symbol in self.trainer.datamodule.symbols:
      train_prediction_data[symbol] = {}
      if val_prediction is not None: val_prediction_data[symbol] = {}
      if test_prediction is not None: test_prediction_data[symbol] = {}

      for f,feature in enumerate(self.trainer.datamodule.output_feature_names[symbol]):
        idx_f = self.trainer.datamodule.output_feature_idx[symbol][f]

        train_prediction_data[symbol][f"{feature}_actual"] = train_target[:, idx_f]
        train_prediction_data[symbol][f"{feature}_prediction"] = train_prediction[:, idx_f]
        if train_baseline_pred is not None:
          train_prediction_data[symbol][f"{feature}_baseline_prediction"] = train_baseline_pred[:, idx_f]

        train_loss_sf = self.loss_fn(train_prediction[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                     train_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
        train_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"] = train_loss_sf
        if train_baseline_pred is not None:
          train_baseline_loss_sf = self.loss_fn(train_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                train_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
          train_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"] = train_baseline_loss_sf

        if self.metric_fn is not None:
          train_metric_sf = self.metric_fn(train_prediction[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                          train_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
          train_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"] = train_metric_sf
          if train_baseline_pred is not None:
            train_baseline_metric_sf = self.metric_fn(train_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                      train_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
            train_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"] = train_baseline_metric_sf

        if val_prediction is not None:
          val_prediction_data[symbol][f"{feature}_actual"] = val_target[:, idx_f]
          val_prediction_data[symbol][f"{feature}_prediction"] = val_prediction[:, idx_f]
          if val_baseline_pred is not None:
            val_prediction_data[symbol][f"{feature}_baseline_prediction"] = val_baseline_pred[:, idx_f]

          val_loss_sf = self.loss_fn(val_prediction[:,idx_f:(idx_f+1)].unsqueeze(0),
                                     val_target[:,idx_f:(idx_f+1)].unsqueeze(0))
          val_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"] = val_loss_sf
          if val_baseline_pred is not None:
            val_baseline_loss_sf = self.loss_fn(val_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                val_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
            val_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"] = val_baseline_loss_sf

          if self.metric_fn is not None:
            val_metric_sf = self.metric_fn(val_prediction[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                           val_target[:,idx_f:(idx_f+1)].unsqueeze(0))
            val_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"] = val_metric_sf
            if val_baseline_pred is not None:
              val_baseline_metric_sf = self.metric_fn(val_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                      val_target[:,idx_f:(idx_f+1)].unsqueeze(0))
              val_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"] = val_baseline_metric_sf

        if test_prediction is not None:
          test_prediction_data[symbol][f"{feature}_actual"] = test_target[:, idx_f]
          test_prediction_data[symbol][f"{feature}_prediction"] = test_prediction[:, idx_f]
          if test_baseline_pred is not None:
            test_prediction_data[symbol][f"{feature}_baseline_prediction"] = test_baseline_pred[:, idx_f]

          test_loss_sf = self.loss_fn(test_prediction[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                      test_target[:,idx_f:(idx_f+1)].unsqueeze(0))
          test_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"] = test_loss_sf
          if test_baseline_pred is not None:
            test_baseline_loss_sf = self.loss_fn(test_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                  test_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
            test_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"] = test_baseline_loss_sf


          if self.metric_fn is not None:
            test_metric_sf = self.metric_fn(test_prediction[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                            test_target[:,idx_f:(idx_f+1)].unsqueeze(0)) 
            test_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"] = test_metric_sf
            if test_baseline_pred is not None:
              test_baseline_metric_sf = self.metric_fn(test_baseline_pred[:,idx_f:(idx_f+1)].unsqueeze(0), 
                                                        test_target[:,idx_f:(idx_f+1)].unsqueeze(0))
              test_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"] = test_baseline_metric_sf

    self.train_prediction_data, self.val_prediction_data, self.test_prediction_data = train_prediction_data, val_prediction_data, test_prediction_data

    self.trainer.enable_progress_bar = True
    self.trainer.datamodule.predicting = False
  ##

  ##
  def evaluate_model(self,
                     loss = 'mse', metric = None):

    loss_name, metric_name = loss, metric

    stride = self.trainer.datamodule.stride

    # loss_name = self.loss_fn.name
    loss_fn = Criterion(loss_name) 

    metric_fn = None
    
    if metric_name is not None:
      # metric_name = self.metric_fn.name
      metric_fn = Criterion(metric_name, 0 if metric_name == 'fb' else None)
      
    if self.test_prediction_data is not None:
      prediction_data = self.test_prediction_data      
    elif self.val_prediction_data is not None:
      prediction_data = self.val_prediction_data
    else:
      prediction_data = self.train_prediction_data

    time = prediction_data[self.trainer.datamodule.time_name]

    self.evaluation_data = {}
    for name in self.trainer.datamodule.output_names:
      
      target = prediction_data[f"{name}_actual"]
      prediction = prediction_data[f"{name}_prediction"]

      # loss
      step_loss = loss_fn(target, prediction)
      global_loss = step_loss.mean(0)
      stride_loss, stride_time = [], []

      self.evaluation_data[f"{name}_step_{loss_name}"] = step_loss
      self.evaluation_data[f"{name}_global_{loss_name}"] = global_loss
      
      for i in range(stride, step_loss.shape[0]+1, stride):
        stride_time.append(time[(i-stride):i])
        stride_loss.append(step_loss[(i-stride):i].mean(0))

      self.evaluation_data[f"{name}_stride_{loss_name}"] = torch.cat(stride_loss,0)
    #

    # metric
    if metric_fn is not None:
      if metric_fn.dims is None:
        step_metric = metric_fn(target, prediction)
        global_metric = step_metric.mean(0)
        stride_loss, stride_time = [], []
        
        for i in range(stride, step_metric.shape[0]+1, stride):
          stride_metric.append(step_metric[(i-stride):i].mean(0))

      else:
        step_metric = None
        global_metric = metric_fn(target, prediction)
          
        stride_metric = []
        for i in range(stride, target.shape[0]+1, stride):
          stride_metric.append(metric_fn(target[(i-stride):i], prediction[(i-stride):i]).reshape(-1, target.shape[-1]))

      self.evaluation_data[f"{name}_step_{metric_name}"] = step_metric
      self.evaluation_data[f"{name}_global_{metric_name}"] = global_metric      
      self.evaluation_data[f"{name}_stride_{metric_name}"] = torch.cat(stride_metric,0)
    
    self.evaluation_data[f"stride_{self.trainer.datamodule.time_name}"] = stride_time
    #
  ##

  ##
  def plot_predictions(self,
                       plot_by_feature = True,
                       output_feature_units = None,
                       include_baseline = False):

    symbols = self.trainer.datamodule.symbols

    time_name = self.trainer.datamodule.time_name
      
    start_step = self.trainer.datamodule.start_step

    rows, cols = np.max([len(x) for x in self.trainer.datamodule.output_feature_names.values()]), len(symbols)
    fig, ax = plt.subplots(rows, cols, figsize = (10*len(symbols), 5*self.trainer.datamodule.max_output_size))

    train_time = self.train_prediction_data[time_name]
    val_time = self.val_prediction_data[time_name] if self.val_prediction_data is not None else None
    test_time = self.test_prediction_data[time_name] if self.test_prediction_data is not None else None

    for s,symbol in enumerate(symbols):
      try:
        ax_s = ax[s, :]
        [ax_s_i.axis("off") for ax_s_i in ax_s]
      except:
        pass

      for f,feature in enumerate(self.trainer.datamodule.output_feature_names[symbol]):

        try:
          ax_sf = ax[f,s]
        except:
          try:
            ax_sf = ax[f]
          except:
            ax_sf = ax

        train_target_sf = self.train_prediction_data[symbol][f"{feature}_actual"]
        train_prediction_sf = self.train_prediction_data[symbol][f"{feature}_prediction"]
        train_loss_sf = np.round(self.train_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"].item(),3)
        train_metric_sf = np.round(self.train_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None
        if include_baseline:
          train_baseline_prediction_sf = self.train_prediction_data[symbol][f"{feature}_baseline_prediction"]
          train_baseline_loss_sf = np.round(self.train_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"].item(),3)
          train_baseline_metric_sf = np.round(self.train_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None

        ax_sf.plot(train_time, train_target_sf, '-k', label = 'Actual')
        ax_sf.plot(train_time, train_prediction_sf, '-r', label = 'Prediction')
        train_label = f"Train ({self.loss_fn.name} = {train_loss_sf}, {self.metric_fn.name} = {train_metric_sf})" \
                      if train_metric_sf is not None \
                      else f"Train ({self.loss_fn.name} = {train_loss_sf})"
        if include_baseline:
          ax_sf.plot(train_time, train_baseline_prediction_sf, '--g', linewidth = 1.0, label = 'Baseline')
          train_label = train_label + f"\nBaseline ({self.loss_fn.name} = {train_baseline_loss_sf}, {self.metric_fn.name} = {train_baseline_metric_sf})"

        ax_sf.axvspan(train_time.min(), train_time.max(), facecolor='gray', alpha=0.2, label = train_label)

        if val_time is not None:
          val_target_sf = self.val_prediction_data[symbol][f"{feature}_actual"]
          val_prediction_sf = self.val_prediction_data[symbol][f"{feature}_prediction"]
          val_loss_sf = np.round(self.val_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"].item(),3)
          val_metric_sf = np.round(self.val_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None
          if include_baseline:
            val_baseline_prediction_sf = self.val_prediction_data[symbol][f"{feature}_baseline_prediction"]
            val_baseline_loss_sf = np.round(self.val_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"].item(),3)
            val_baseline_metric_sf = np.round(self.val_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None

          ax_sf.plot(val_time, val_target_sf, '-k')
          ax_sf.plot(val_time, val_prediction_sf, '-r')
          val_label = f"Val ({self.loss_fn.name} = {val_loss_sf}, {self.metric_fn.name} = {val_metric_sf})" \
                        if val_metric_sf is not None \
                        else f"Val ({self.loss_fn.name} = {val_loss_sf})"
          if include_baseline:
            ax_sf.plot(val_time, val_baseline_prediction_sf, '--g', linewidth = 1.0)
            val_label = val_label + f"\nBaseline ({self.loss_fn.name} = {val_baseline_loss_sf}, {self.metric_fn.name} = {val_baseline_metric_sf})"

          ax_sf.axvspan(val_time.min(), val_time.max(), facecolor='blue', alpha=0.2, label = val_label)

        if test_time is not None:
          test_target_sf = self.test_prediction_data[symbol][f"{feature}_actual"]
          test_prediction_sf = self.test_prediction_data[symbol][f"{feature}_prediction"]
          test_loss_sf = np.round(self.test_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"].item(),3)
          test_metric_sf = np.round(self.test_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None
          if include_baseline:
            test_baseline_prediction_sf = self.test_prediction_data[symbol][f"{feature}_baseline_prediction"]
            test_baseline_loss_sf = np.round(self.test_prediction_data[symbol][f"{feature}_baseline_{self.loss_fn.name}"].item(),3)
            test_baseline_metric_sf = np.round(self.test_prediction_data[symbol][f"{feature}_baseline_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None

          ax_sf.plot(test_time, test_target_sf, '-k')
          ax_sf.plot(test_time, test_prediction_sf, '-r')
          test_label = f"Test ({self.loss_fn.name} = {test_loss_sf}, {self.metric_fn.name} = {test_metric_sf})" \
                        if test_metric_sf is not None \
                        else f"Test ({self.loss_fn.name} = {test_loss_sf})"
          if include_baseline:
            ax_sf.plot(test_time, test_baseline_prediction_sf, '--g', linewidth = 1.0)
            test_label = test_label + f"\nBaseline ({self.loss_fn.name} = {test_baseline_loss_sf}, {self.metric_fn.name} = {test_baseline_metric_sf})"

          ax_sf.axvspan(test_time.min(), test_time.max(), facecolor='green', alpha=0.2, label = test_label)

        if f == 0:
          ax_sf.set_title(symbol)
        if f == len(self.trainer.datamodule.output_feature_names[symbol]) - 1:
          ax_sf.set_xlabel(f"Time [{self.trainer.datamodule.time_unit}]")
        ylabel = f"{feature} [{output_feature_units[symbol][f]}]" if output_feature_units is not None else f"{feature}"

        ax_sf.set_ylabel(ylabel)

        ax_sf.legend(loc='upper left', bbox_to_anchor=(1.02, 1), ncol=1) # loc = 'upper center', bbox_to_anchor = (0.5, 1.15), ncol = 5))

    if len(symbols) > 1:
      for i in range(len(symbols), rows):
          ax[i].axis("off")

    fig.tight_layout()

    self.actual_prediction_plot = plt.gcf()
  ##

  def plot_residuals(self, widths = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0], output_feature_units = None):

    symbols = self.trainer.datamodule.symbols

    rows, cols = np.max([len(x) for x in self.trainer.datamodule.output_feature_names.values()]), len(symbols)
    fig, ax = plt.subplots(rows, cols, figsize = (10*len(symbols), 5*self.trainer.datamodule.max_output_size))

    for s,symbol in enumerate(symbols):
      try:
        ax_s = ax[s, :]
        [ax_s_i.axis("off") for ax_s_i in ax_s]
      except:
        pass

      for f,feature in enumerate(self.trainer.datamodule.output_feature_names[symbol]):

        try:
          ax_sf = ax[f,s]
        except:
          ax_sf = ax[f]

        test_target_sf = self.test_prediction_data[symbol][f"{feature}_actual"]
        test_prediction_sf = self.test_prediction_data[symbol][f"{feature}_prediction"]

        test_residual = test_target_sf - test_prediction_sf

        # Compute histogram
        hist, bin_edges = np.histogram(test_residual, bins='auto')

        # Plot histogram
        ax_sf.hist(test_residual, bins='auto', color = 'k', alpha = 0.2)

        test_residual_len = len(test_residual)

        color_cycle = plt.rcParams['axes.prop_cycle'].by_key()['color']

        for k,w in enumerate(widths):

          test_residual_width_in_w = (test_residual.abs() <= w).sum().item()
          pct_test_residual_width_in_w = np.round(test_residual_width_in_w/test_residual_len * 100,1)

          i = np.where(hist.cumsum() >= test_residual_width_in_w)[0].argmax()
          j = np.where(hist >= hist[i])[0].max() + 1

          start = bin_edges[i]
          end = bin_edges[j]

          # Plot a horizontal line at the height of the maximum frequency
          ax_sf.hlines(hist[i], start, end, colors=color_cycle[k % len(color_cycle)], linestyles='-', label=f"{pct_test_residual_width_in_w}% = {w}")

        if f == 0:
          ax_sf.set_title(symbol)
        ax_sf.set_ylabel(f"{feature} Frequency")

        if output_feature_units is not None: ax_sf.set_xlabel(output_feature_units[symbol][f])

        ax_sf.legend(loc='upper left', bbox_to_anchor=(1.02, 1), ncol=1)
        ax_sf.grid()
        #

      fig.tight_layout()
      self.residual_histogram = plt.gcf()
  ##
  
  ##  
  def report(self, file_path):

    current_time = datetime.now().strftime(self.trainer.datamodule.date_format) # "%Y-%m-%d-%H-%M-%S"

    file_path = f"{dir}/{current_time}_predictions.csv"

    time_name = self.trainer.datamodule.time_name  
    # Define the column index hierarchy
    metrics =  ["Actual", "Prediction", self.loss_fn.name, self.metric_fn.name] \
               if self.metric_fn.name is not None \
               else ["Actual", "Prediction", self.loss_fn.name]
    columns = pd.MultiIndex.from_tuples([(time_name, '', ''),
                                        *[(symbol, feature, metric)
                                        for symbol in self.trainer.datamodule.symbols \
                                        for feature in self.trainer.datamodule.output_feature_names[symbol] \
                                        for metric in metrics
                                        ]])

    df = pd.DataFrame(columns=columns)

    test_time = self.test_prediction_data[time_name]

    j = 0
    for symbol in self.trainer.datamodule.symbols:
      for feature in self.trainer.datamodule.output_feature_names[symbol]:

        test_target_sf = self.test_prediction_data[symbol][f"{feature}_actual"]
        test_prediction_sf = self.test_prediction_data[symbol][f"{feature}_prediction"]
        test_loss_sf = np.round(self.test_prediction_data[symbol][f"{feature}_{self.loss_fn.name}"].item(),3)
        test_metric_sf = np.round(self.test_prediction_data[symbol][f"{feature}_{self.metric_fn.name}"].item(),3) if self.metric_fn is not None else None

        df[(time_name, '', '')] = pd.Series(test_time)
        df[(symbol, feature, "Actual")] = pd.Series(test_target_sf.numpy())
        df[(symbol, feature, "Prediction")] = pd.Series(test_prediction_sf.numpy())
        df[(symbol, feature, self.loss_fn.name)] = Criterion(self.loss_fn.name, reduction=None)(test_prediction_sf, test_target_sf)
        if self.metric_fn is not None:
          df[(symbol, feature, self.metric_fn.name)] = Criterion(self.metric_fn.name, reduction=None)(test_prediction_sf, test_target_sf)

    return df
  ##
   
  ## forecast
  def forecast(self, num_forecast_steps = 1, hiddens = None):
    
    self.model.to(device = self.trainer.datamodule.device,
                  dtype = self.trainer.datamodule.dtype)
    
    with torch.no_grad():
      steps = None
      
      forecast_dl = self.trainer.datamodule.forecast_dataloader()
      
      for batch in forecast_dl: last_sample = batch
      
      forecast_time = self.trainer.datamodule.dt + torch.arange(num_forecast_steps) * self.trainer.datamodule.dt + self.trainer.datamodule.last_time
      # forecast_time = self.trainer.datamodule.last_time + pd.to_timedelta(np.arange(num_forecast_steps) * self.trainer.datamodule.dt)

      input, _, steps, batch_size = last_sample

      last_input_sample, last_steps_sample = input[:batch_size][-1:], steps[:batch_size][-1:]

      input_window_idx = self.trainer.datamodule.forecast_input_window_idx
      output_window_idx = self.trainer.datamodule.forecast_output_window_idx
      max_output_len = self.trainer.datamodule.forecast_max_output_len
      total_input_size, total_output_size = np.sum(self.trainer.datamodule.input_size), np.sum(self.trainer.datamodule.output_size)
      output_mask = self.trainer.datamodule.forecast_output_mask
      output_input_idx, input_output_idx = self.trainer.datamodule.output_input_idx, self.trainer.datamodule.input_output_idx
      
      max_input_window_idx = np.max([idx.max().cpu() for idx in input_window_idx])
      max_output_window_idx = np.max([idx.max().cpu() for idx in output_window_idx])

      forecast_len = np.max([1, max_output_window_idx - max_input_window_idx + 1])

      input, steps = last_input_sample, last_steps_sample

      forecast = torch.empty((1, 0, total_output_size)).to(device = self.model.device,
                                                         dtype = self.model.dtype)
      forecast_steps = torch.empty((1, 0)).to(device = self.model.device,
                                              dtype = torch.long)

      output, hiddens = self.forward(input = last_input_sample,
                                     steps = last_steps_sample,
                                     hiddens = hiddens,
                                     input_window_idx = input_window_idx,
                                     output_window_idx = output_window_idx,
                                     output_mask = output_mask,
                                     output_input_idx = output_input_idx, input_output_idx = input_output_idx)

      forecast = torch.cat((forecast, output[:, -forecast_len:]), 1)
      forecast_steps = torch.cat((forecast_steps, steps[:, -forecast_len:]), 1)

      steps += forecast_len

      while forecast.shape[1] < num_forecast_steps:

        input_ = torch.zeros((1, forecast_len, total_input_size)).to(input)

        if len(output_input_idx) > 0:
          input_[:, :, output_input_idx] = output[:, -forecast_len:, input_output_idx]

        input = torch.cat((input[:, forecast_len:], input_), 1)

        output, hiddens = self.forward(input = input,
                                       steps = steps,
                                       hiddens = hiddens,
                                       input_window_idx = input_window_idx,
                                       output_window_idx = output_window_idx,
                                       output_mask = output_mask,
                                       output_input_idx = output_input_idx, input_output_idx = input_output_idx)

        forecast = torch.cat((forecast, output[:, -forecast_len:]), 1)
        forecast_steps = torch.cat((forecast_steps, steps[:, -forecast_len:]), 1)

        steps += forecast_len

      forecast, forecast_steps = forecast[:, -num_forecast_steps:], forecast_steps[:, -num_forecast_steps:]
      forecast_reduced, forecast_steps_reduced = self.generate_reduced_output(forecast, forecast_steps,
                                                                          transforms=self.trainer.datamodule.transforms)

    return forecast_reduced, forecast_time
  ##

  ##
  def evaluate_forecast(self, num_forecast_steps = 1, hiddens = None):

    if len(self.trainer.datamodule.test_data) > 0:   
      if not hasattr(self.trainer.datamodule, 'test_dl'): 
        self.trainer.datamodule.predicting = True
        self.trainer.datamodule.test_dataloader()
        self.trainer.datamodule.predicting = False

      forecast_dl = self.trainer.datamodule.test_dl.dl  
      input_window_idx = self.trainer.datamodule.test_input_window_idx
      output_window_idx = self.trainer.datamodule.test_output_window_idx
      max_input_len, max_output_len = self.trainer.datamodule.test_max_input_len, self.trainer.datamodule.test_max_output_len
      output_mask = self.trainer.datamodule.test_output_mask

    elif len(self.trainer.datamodule.val_data) > 0:    
      if not hasattr(self.trainer.datamodule, 'val_dl'): 
        self.trainer.datamodule.predicting = True
        self.trainer.datamodule.val_dataloader()
        self.trainer.datamodule.predicting = False

      forecast_dl = self.trainer.datamodule.val_dl.dl
      input_window_idx = self.trainer.datamodule.val_input_window_idx
      output_window_idx = self.trainer.datamodule.val_output_window_idx
      max_input_len, max_output_len = self.trainer.datamodule.val_max_input_len, self.trainer.datamodule.val_max_output_len
      output_mask = self.trainer.datamodule.val_output_mask
    
    else:        
      if not hasattr(self.trainer.datamodule, 'train_dl'): 
        self.trainer.datamodule.predicting = True
        self.trainer.datamodule.train_dataloader()
        self.trainer.datamodule.predicting = False

      forecast_dl = self.trainer.datamodule.train_dl.dl
      input_window_idx = self.trainer.datamodule.train_input_window_idx
      output_window_idx = self.trainer.datamodule.train_output_window_idx
      max_input_len, max_output_len = self.trainer.datamodule.train_max_input_len, self.trainer.datamodule.train_max_output_len
      output_mask = self.trainer.datamodule.train_output_mask

    output_input_idx, input_output_idx = self.trainer.datamodule.input_output_idx, self.trainer.datamodule.input_output_idx

    time = self.trainer.datamodule.data[self.trainer.datamodule.time_name]

    start_step = self.trainer.datamodule.start_step

    unique_output_window_idx = torch.cat(output_window_idx).unique()

    hiddens = None
    input, target, steps = [], [], []
    for batch in forecast_dl: 

      input_batch, target_batch, steps_batch, batch_size = batch
      
      input_batch, target_batch, steps_batch = input_batch[:batch_size], target_batch[:batch_size], steps_batch[:batch_size]

      input.append(input_batch)
      target.append(target_batch)
      steps.append(steps_batch)
      
    input, target, steps = torch.cat(input), torch.cat(target), torch.cat(steps)

    output_steps = steps[:, unique_output_window_idx]  

    self.forecast_time = [[time.iloc[s - start_step]] for s in output_steps]

    num_samples = input.shape[0]

    max_input_window_idx = np.max([idx.max().cpu() for idx in input_window_idx])
    max_output_window_idx = np.max([idx.max().cpu() for idx in output_window_idx])
    max_output_size = np.max(self.model.output_size)

    forecast_len = np.max([1, max_output_window_idx - max_input_window_idx + 1])

    forecast = torch.empty((num_samples, 0, max_output_size)).to(device = self.model.device,
                                                                dtype = self.model.dtype)
    forecast_steps = torch.empty((num_samples, 0)).to(device = self.model.device,
                                                      dtype = torch.long)

    output, hiddens = self.forward(input = input,
                                          steps = steps,
                                          hiddens = hiddens,
                                          input_window_idx = input_window_idx,
                                          output_window_idx = output_window_idx,
                                          output_mask = output_mask,
                                          output_input_idx = output_input_idx, 
                                          input_output_idx = input_output_idx)
    
    forecast = torch.cat((forecast, output[:, -forecast_len:]), 1)
    forecast_steps = torch.cat((forecast_steps, steps[:, -forecast_len:]), 1)

    steps += forecast_len

    while forecast.shape[1] < num_forecast_steps:

      input_ = torch.zeros((1, forecast_len, max_output_size)).to(input)

      if len(output_input_idx) > 0:
        input_[:, :, output_input_idx] = output[:, -forecast_len:, input_output_idx]

      input = torch.cat((input[:, forecast_len:], input_), 1)

      output, hiddens = self.forward(input = input,
                                            steps = steps,
                                            hiddens = hiddens,
                                            input_window_idx = input_window_idx,
                                            output_window_idx = output_window_idx,
                                            output_mask = output_mask,
                                            output_input_idx = output_input_idx, 
                                            input_output_idx = input_output_idx)

      forecast = torch.cat((forecast, output[:, -forecast_len:]), 1)
      forecast_steps = torch.cat((forecast_steps, steps[:, -forecast_len:]), 1)

      steps += forecast_len

    j = 0
    for i,name in enumerate(self.trainer.datamodule.output_names):
      forecast[..., j:(j+self.model.output_size[i])] = self.trainer.datamodule.transforms[name].inverse_transform(forecast[..., j:(j+self.model.output_size[i])])
      target[..., j:(j+self.model.output_size[i])] = self.trainer.datamodule.transforms[name].inverse_transform(target[..., j:(j+self.model.output_size[i])])

    self.forecast, self.forecast_target = forecast, target

    self.forecast_loss = Criterion(self.loss_fn.name, dims = 1)(forecast, target)
    self.forecast_metric = None
    if self.metric_fn.name is not None:
      self.forecast_metric = Criterion(self.metric_fn.name, dims = 1)(forecast, target)    
      ##

  ##
  def generate_reduced_output(self, output, output_steps, reduction='mean', transforms=None):

    # Get unique output steps and remove any -1 values
    unique_output_steps = output_steps.unique()
    unique_output_steps = unique_output_steps[unique_output_steps != -1]

    # Create a tensor to store the reduced output
    output_reduced = torch.zeros((len(unique_output_steps), np.sum(self.model.output_size))).to(output)

    output_names = self.trainer.datamodule.output_feature_names or self.trainer.datamodule.output_names

    k = -1
    for step in unique_output_steps:
        k += 1

        # Find the indices of the current step in the output_steps tensor
        batch_step_idx = torch.where(output_steps == step)
        num_step_output = len(batch_step_idx[0])

        j = 0
        for i in range(self.model.num_outputs):

            # Extract the output for the current output index
            output_i = output[:, :, j:(j + self.model.output_size[i])]
            output_reduced_i = []

            step_output_i = []
            for batch_idx, step_idx in zip(*batch_step_idx[:2]):
                step_output_i.append(output_i[batch_idx, step_idx, :].reshape(1, 1, -1))

            if len(step_output_i) > 0:
                step_output_i = torch.cat(step_output_i, 0)

                # Reduce the step outputs based on the specified reduction method
                step_output_reduced_i = (step_output_i.median(0)[0] if reduction == 'median' else
                                         step_output_i.mean(0)).reshape(-1, self.model.output_size[i])

                # Assign the reduced output to the output_reduced tensor
                output_reduced[k, j:(j + self.model.output_size[i])] = step_output_reduced_i.squeeze(0)

            j += self.model.output_size[i]

    # Optionally invert the reduced output using data scalers
    if transforms is not None:
        j = 0
        for i in range(self.model.num_outputs):
          print('output_names[i]', output_names[i])
          output_name_i = output_names[i]
          output_reduced[:, j:(j + self.model.output_size[i])] = transforms[output_name_i].inverse_transform(output_reduced[:, j:(j + self.model.output_size[i])])
          j += self.model.output_size[i]

    # Return the reduced output and unique output steps
    return output_reduced, unique_output_steps
  ##
  
  ##
  def fit(self,
          datamodule,
          max_epochs = 20,
          callbacks = [None]):

    try:
      self.trainer = pl.Trainer(max_epochs = max_epochs,
                                accelerator = 'gpu' if self.model.device == 'cuda' else 'cpu',
                                callbacks = callbacks)

      self.trainer.fit(self,
                       datamodule = datamodule)

    except KeyboardInterrupt:
      state_dict = self.model.state_dict()
      self.model.to(device = self.model.device,
                    dtype = self.model.dtype)
      self.model.load_state_dict(state_dict)
  ##
