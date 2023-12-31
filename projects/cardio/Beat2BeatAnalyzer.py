import numpy as np
from sklearn.cluster import KMeans

import scipy as sc

import matplotlib.pyplot as plt

import importlib

from TorchTimeSeries.ts_src import butter, periodogram, moving_average, Interpolator

class Beat2BeatAnalyzer():
  def __init__(self, dt, ecg, abp):

    self.dt = dt
    self.ecg, self.abp = ecg, abp
    self.t = np.arange(len(ecg))*self.dt

  def fill(self, interp_kind = 'linear'):

    interpolator = Interpolator(kind = interp_kind)

    if np.any(np.isnan(self.ecg)):

      ecg_notnan = self.ecg[~np.isnan(self.ecg)]
      t_notnan = self.t[~np.isnan(self.ecg)]

      interpolator.fit(t_notnan, ecg_notnan)

      self.ecg = interpolator.interp_fn(self.t)

    if np.any(np.isnan(self.abp)):

      abp_notnan = self.abp[~np.isnan(self.abp)]
      t_notnan = self.t[~np.isnan(self.abp)]

      interpolator.fit(t_notnan, abp_notnan)

      self.abp = interpolator.interp_fn(self.t)

  def filter(self,
             ecg_critical_frequency = np.array([1, 20]), ecg_butter_type = 'bandpass', ecg_filter_order = 3,
             abp_critical_frequency = 20, abp_butter_type = 'low', abp_filter_order = 3):

    self.ecg = butter(self.ecg, critical_frequency = ecg_critical_frequency,
                        butter_type = ecg_butter_type, filter_order = ecg_filter_order,
                        sampling_rate = 1/self.dt)
    self.abp = butter(self.abp, critical_frequency = abp_critical_frequency,
                        butter_type = abp_butter_type, filter_order = abp_filter_order,
                        sampling_rate = 1/self.dt)

  def get_beat2beat_features(self,
                            z_ecg_amp_critical = 5,
                            window = 0.5,
                            min_prominence = 0.3,
                            min_interval = 0.6, max_interval = 2, y_interval_critical = 1.5):

    ecg, abp = self.ecg, self.abp
    ecg = ecg/ecg.max()

    ecg_d2 = np.diff(np.pad(ecg, (2, 0), mode='edge'), 2)
    ecg_d3 = np.diff(np.pad(ecg, (3, 0), mode='edge'), 3)

    i_ecg_peaks, p_ecg_peaks  = sc.signal.find_peaks(ecg, prominence = 0)
    i_ecg_troughs, _  = sc.signal.find_peaks(-ecg, prominence = 0)
    p_ecg_peaks = p_ecg_peaks['prominences']

    i_ecg_troughs = i_ecg_troughs[i_ecg_troughs > i_ecg_peaks.min()]
    p_ecg_peaks = p_ecg_peaks[i_ecg_peaks < i_ecg_troughs.max()]
    i_ecg_peaks = i_ecg_peaks[i_ecg_peaks < i_ecg_troughs.max()]

    i_ecg_troughs = i_ecg_troughs[ecg_d2[i_ecg_peaks] < 0]
    p_ecg_peaks = p_ecg_peaks[(ecg_d2[i_ecg_peaks] < 0)]
    i_ecg_peaks = i_ecg_peaks[ecg_d2[i_ecg_peaks] < 0]

    i_ecg_peaks_all, i_ecg_troughs_all = i_ecg_peaks, i_ecg_troughs

    threshold = np.inf

    loop = True
    num_loops = 0
    while loop & (num_loops < 1):

      num_loops += 1

      p_data = p_ecg_peaks.reshape(-1, 1)

      kmeans = KMeans(n_clusters=2, n_init = 'auto')
      kmeans.fit(p_data)
      centers = kmeans.cluster_centers_
      labels = kmeans.labels_

      cluster_sizes = np.bincount(labels)

      valid_cluster_idx = np.where(cluster_sizes >= 20)[0]

      centers = centers[valid_cluster_idx]

      i_ecg_troughs = i_ecg_troughs[np.isin(labels, valid_cluster_idx)]
      i_ecg_peaks = i_ecg_peaks[np.isin(labels, valid_cluster_idx)]
      p_ecg_peaks = p_ecg_peaks[np.isin(labels, valid_cluster_idx)]

      p_data = p_data[np.isin(labels, valid_cluster_idx)]
      labels = labels[np.isin(labels, valid_cluster_idx)]

      max_center_idx = np.argmax(np.sum(centers, axis=1))
      max_center = centers[max_center_idx]
      max_center_data = p_data[labels == max_center_idx]

      sdev = max_center_data.std()
      threshold = max_center - 5*sdev

      loop = (sum(p_ecg_peaks < threshold)/len(p_ecg_peaks))*100 > 10

      # plt.close()
      # fig, ax = plt.subplots(3, 1, figsize = (20, 10))
      # xlim = [400, 430] # [0, 10/self.dt]
      # ax[0].plot(np.arange(len(ecg))*self.dt, ecg, '-')
      # ax[0].plot(i_ecg_peaks*self.dt, ecg[i_ecg_peaks], '.g', label = 'ecg peaks')
      # ax[0].legend()
      # ax[0].set_xlim(xlim)

      # ax[1].plot(i_ecg_peaks, p_ecg_peaks, '*g', label = 'ecg peak prominence')
      # ax[1].axhline(y=threshold, color='red', linestyle='--')
      # ax[1].legend()

      # ax[2].scatter(p_data, np.zeros_like(p_data), c=labels, cmap='viridis')
      # ax[2].scatter(centers, np.zeros_like(centers), marker='x', color='red', label='Centers')
      # ax[2].axvline(x=threshold, color='red', linestyle='--')
      # ax[2].set_xlabel('Prominences')
      # ax[2].set_title('K-means Clustering of ECG Prominences (K=2)')
      # ax[2].legend()

      # plt.tight_layout()

      i_ecg_troughs = i_ecg_troughs[p_ecg_peaks > threshold] # [labels == max_center_idx] #
      i_ecg_peaks = i_ecg_peaks[p_ecg_peaks > threshold] # [labels == max_center_idx] #
      p_ecg_peaks = p_ecg_peaks[p_ecg_peaks > threshold] # [labels == max_center_idx] #

      i_ecg_peaks_all, i_ecg_troughs_all = i_ecg_peaks, i_ecg_troughs

    # plt.close(fig = 1)
    # plt.figure(num = 1)
    # plt.plot(np.arange(len(ecg)), ecg, '-') ;
    # plt.plot(i_ecg_peaks, ecg[i_ecg_peaks], '.b', label = f'{len(i_ecg_peaks)} ecg peaks') ;
    # plt.plot(i_ecg_troughs, ecg[i_ecg_troughs], '.r', label = f'{len(i_ecg_troughs)} ecg troughs') ;
    # plt.xlim(np.array([400, 415])/self.dt) ;
    # plt.legend() ;

    ##
    interval = np.diff(i_ecg_peaks)*self.dt
    z_interval = (interval - interval.mean())/interval.std()
    y_interval = interval/np.median(interval)
    i_near = np.where((interval < min_interval) | (y_interval < 1/y_interval_critical))[0]

    while i_near.size != 0:

      i_nears = i_near[0] + [0, 1]

      i_discard = i_nears[(ecg[i_ecg_peaks[i_nears]] - ecg[i_ecg_troughs[i_nears]]).argmin()]

      i_ecg_troughs = np.delete(i_ecg_troughs, i_discard)
      i_ecg_peaks = np.delete(i_ecg_peaks, i_discard)

      interval = np.diff(i_ecg_peaks)*self.dt
      z_interval = (interval - interval.mean())/interval.std()
      y_interval = interval/np.median(interval)
      i_near =np.where((interval < min_interval) | (y_interval < 1/y_interval_critical))[0]
    ##

    hr = 60/interval

    # plt.close(fig = 2)
    # fig, ax = plt.subplots(2, 1, figsize = (20, 5), num = 2) ;
    # xlim = np.array([400, 430])
    # ax[0].plot(np.arange(len(ecg))*self.dt, ecg) ;
    # ax[0].plot(i_ecg_peaks*self.dt, ecg[i_ecg_peaks],'.b') ;
    # ax[0].plot(i_ecg_troughs*self.dt, ecg[i_ecg_troughs],'.r') ;
    # ax[0].set_xlim(xlim)

    # ax[1].plot(i_ecg_peaks[1:]*self.dt, interval) ;
    # ax[1].set_xlim(xlim)
    
    i_ecg_r = i_ecg_peaks

    i_ecg_troughs_fill = i_ecg_troughs_all[~np.isin(i_ecg_peaks_all, i_ecg_r)]
    i_ecg_peaks_fill = i_ecg_peaks_all[~np.isin(i_ecg_peaks_all, i_ecg_r)]

    ##
    interval = np.diff(i_ecg_r)*self.dt
    y_interval = interval/np.median(interval)
    i_far = np.where((interval > max_interval) | (y_interval > y_interval_critical))[0]

    k,j = -1,0
    while (i_far.size != 0) & (j>k): #

      k = np.min(i_far)

      for i in range(len(i_far)):

        j_far = i_far[i] + [0, 1]

        j_ecg_peaks = i_ecg_peaks_fill[(i_ecg_peaks_fill > i_ecg_r[j_far[0]]) & (i_ecg_peaks_fill < i_ecg_r[j_far[1]])]

        i_ecg_r_fill = None
        if len(j_ecg_peaks) > 0:
          i_ecg_r_fill = j_ecg_peaks[ecg[j_ecg_peaks].argmax()]

          # i_ecg_r = np.unique(np.concatenate(i_ecg_r, i_ecg_r_fill))
          idx = np.searchsorted(i_ecg_r, i_ecg_r_fill)
          i_ecg_r = np.unique(np.insert(i_ecg_r, idx, i_ecg_r_fill))

          i_ecg_peaks_fill = i_ecg_peaks_fill[~np.isin(i_ecg_peaks_fill, i_ecg_r)]

        # plt.close()
        # fig, ax = plt.subplots(2, 1, figsize = (20, 5)) ;
        # xlim = [400, 415]
        # ax[0].plot(np.arange(len(ecg))*self.dt,ecg) ;
        # ax[0].plot(i_ecg_r*self.dt, ecg[i_ecg_r],'.b') ;
        # if i_ecg_r_fill is not None:
        #   ax[0].plot(i_ecg_r_fill*self.dt, ecg[i_ecg_r_fill],'or') ;
        # ax[0].set_xlim(xlim)

        # # ax[1].plot(y_interval) ;
        # # ax[1].set_xlim(xlim)

        # if i_ecg_r_fill is not None:
        #   if 400 < i_ecg_r_fill*self.dt < 415: dfdf

      interval = np.diff(i_ecg_r)*self.dt
      y_interval = interval/np.median(interval)
      i_far = np.where((interval > max_interval) | (y_interval > y_interval_critical))[0]

      j = np.min(i_far[0]) if i_far.size != 0 else -1

    hr = 60/interval
    ##

    # plt.close()
    # fig, ax = plt.subplots(2, 1, figsize = (20, 5)) ;
    # xlim = [None, None]
    # ax[0].plot(np.arange(len(ecg))*self.dt,ecg) ;
    # ax[0].plot(i_ecg_r*self.dt, ecg[i_ecg_r],'.') ;
    # ax[0].set_xlim(xlim)

    # ax[1].plot(i_ecg_r[1:]*self.dt, interval) ;
    # ax[1].set_xlim(xlim)

    interval = np.diff(i_ecg_r)*self.dt
    hr = 60/interval
    self.i_ecg_r = i_ecg_r
    self.interval, self.hr = interval, hr
    self.beat_dt = self.interval.mean().round(2)
    self.beat_t = self.interval.cumsum()

    ##
    i_abp_peaks, i_abp_troughs = sc.signal.find_peaks(abp)[0], sc.signal.find_peaks(-abp)[0]

    i_sbp, i_dbp, mabp = [], [], []
    i = 1
    updated_i_ecg_r = [i_ecg_r[0]]
    while i < len(i_ecg_r):
      j_abp_troughs = i_abp_troughs[(i_abp_troughs > updated_i_ecg_r[-1]) & (i_abp_troughs <= i_ecg_r[i])]
      j_abp_peaks = i_abp_peaks[(i_abp_peaks > updated_i_ecg_r[-1]) & (i_abp_peaks <= i_ecg_r[i])]

      if (len(j_abp_troughs) == 0) or (len(j_abp_peaks) == 0):
        i_ecg_r_min = i_ecg_r[(i-1):i][ecg[i_ecg_r[(i-1):i]].argmin()]
        i_ecg_r = np.delete(i_ecg_r, np.where(i_ecg_r == i_ecg_r_min))

        interval = np.diff(i_ecg_r)*self.dt
        hr = 60/interval
        self.i_ecg_r = i_ecg_r
        self.interval, self.hr = interval, hr
        self.beat_dt = self.interval.mean().round(2)
        self.beat_t = self.interval.cumsum()

        # plt.close()
        # fig, ax = plt.subplots(2,1)
        # ax[0].plot(ecg)
        # ax[0].plot(i_ecg_r, ecg[i_ecg_r], '.')
        # ax[0].set_xlim([i_ecg_r[i]-30/self.dt, i_ecg_r[i]+30/self.dt])

        # ax[1].plot(abp)
        # ax[1].plot(i_dbp,abp[i_dbp],'.')
        # ax[1].plot(i_sbp,abp[i_sbp],'.')
        # ax[1].set_xlim([i_ecg_r[i]-30/self.dt, i_ecg_r[i]+30/self.dt])

      else:
        updated_i_ecg_r.append(i_ecg_r[i])

        i_dbp.append(j_abp_troughs[abp[j_abp_troughs].argmin()])
        i_sbp.append(j_abp_peaks[abp[j_abp_peaks].argmax()])

        mabp.append(abp[i_ecg_r[i-1]:(i_ecg_r[i]+1)].mean())

        i += 1

    i_ecg_r = np.array(updated_i_ecg_r)
    i_sbp, i_dbp = np.array(i_sbp), np.array(i_dbp)
    ##

    # plt.close()
    # fig, ax = plt.subplots(2, 1)
    # xlim = [None, None] # [0, 5/self.dt]
    # ax[0].plot(ecg)
    # ax[0].plot(i_ecg_r, ecg[i_ecg_r], '.g')
    # ax[0].set_xlim(xlim)

    # ax[1].plot(abp)
    # ax[1].plot(i_sbp, abp[i_sbp], '.g')
    # ax[1].plot(i_dbp, abp[i_dbp], '.r')
    # ax[1].set_xlim(xlim)
    #
    # plt.tight_layout()

    sbp, dbp = abp[i_sbp], abp[i_dbp]
    mabp = np.array(mabp)

    # i_sbp, i_dbp = i_sbp[:-1], i_dbp[:-1]
    # i_ecg_r = i_ecg_r[1:]

    self.sbp, self.dbp, self.i_sbp, self.i_dbp, self.mabp = sbp, dbp, i_sbp, i_dbp, mabp

  def generate_beat2beat_variability(self, window_type = 'hann', moving_average_window_len = 120):

    if window_type == 'hann':
      window = sc.signal.windows.hann(moving_average_window_len)
    elif window_type == 'hamming':
      window = sc.signal.windows.hamming(moving_average_window_len)

    self.sbp_ma, self.dbp_ma = moving_average(self.sbp, window).numpy(), moving_average(self.dbp, window).numpy()
    self.mabp_ma = moving_average(self.mabp, window).numpy()

    self.hr_ma = moving_average(self.hr, window).numpy()
    self.interval_ma = moving_average(self.interval, window).numpy()

    self.sbpv, self.dbpv = self.sbp - self.sbp_ma, self.dbp - self.dbp_ma
    self.mabpv = self.mabp - self.mabp_ma
    self.hrv = self.hr - self.hr_ma
    self.intervalv = self.interval - self.interval_ma

    self.sbpv, self.dbpv, self.mabpv = self.sbpv - self.sbpv.mean(), self.dbpv - self.dbpv.mean(), self.mabpv - self.mabpv.mean()
    self.hrv, self.intervalv = self.hrv - self.hrv.mean(), self.intervalv - self.intervalv.mean()

  def remove_extreme_changes(self,
                      max_mabp_change = 20, z_mabp_change_critical = 4,
                      max_hr_change = 0.1, z_hr_change_critical = 4,
                      interp_type = 'linear'):

    hr, interval, mabp = self.hr.copy(), self.interval.copy(), self.mabp.copy()

    ## hr/interval
    i_hr_all = np.arange(len(hr),  dtype = np.compat.long)
    i_hr = i_hr_all

    hr_diff = np.diff(hr,1,0)
    z_hr_diff = (hr_diff - hr_diff.mean())/hr_diff.std()

    i_discard = np.where((np.abs(z_hr_diff) > z_hr_change_critical) | (np.abs(hr_diff) > max_hr_change))[0]

    if len(i_discard):
      hr_interpolator = Interpolator(kind = interp_type)
      interval_interpolator = Interpolator(kind = interp_type)
    else:
      hr_interpolator = None
      interval_interpolator = None

    while len(i_discard) > 0:

      j_discard = i_discard[0] + [0, 1]
      j_discard = j_discard[j_discard < len(hr)]

      j_discard = j_discard[np.abs(hr.mean() - hr[j_discard]).argmax()]
      hr = np.delete(hr, j_discard)
      interval = np.delete(interval, j_discard)
      i_hr = np.delete(i_hr, j_discard)

      hr_diff = np.diff(hr,1,0)
      z_hr_diff = (hr_diff - hr_diff.mean())/hr_diff.std()

      i_discard = np.where((np.abs(z_hr_diff) > z_hr_change_critical) | (np.abs(hr_diff) > max_hr_change))[0]
    ##

    # plt.figure(1)
    # plt.plot(i_hr_all, self.hr)
    # plt.plot(i_hr, hr)

    ## mabp
    i_mabp_all = np.arange(len(mabp),  dtype = np.compat.long)
    i_mabp = i_mabp_all

    mabp_diff = np.diff(mabp,1,0)
    z_mabp_diff = (mabp_diff - mabp_diff.mean())/mabp_diff.std()

    i_discard = np.where((np.abs(z_mabp_diff) > z_mabp_change_critical) | (np.abs(mabp_diff) > max_mabp_change))[0]

    if len(i_discard):
      mabp_interpolator = Interpolator(kind = interp_type)
    else:
      mabp_interpolator = None

    while len(i_discard) > 0:

      j_discard = i_discard[0] + [0, 1]
      j_discard = j_discard[j_discard < len(mabp)]

      j_discard = j_discard[np.abs(mabp.mean() - mabp[j_discard]).argmax()]
      mabp = np.delete(mabp, j_discard)
      i_mabp = np.delete(i_mabp, j_discard)

      mabp_diff = np.diff(mabp,1,0)
      z_mabp_diff = (mabp_diff - mabp_diff.mean())/mabp_diff.std()

      i_discard = np.where((np.abs(z_mabp_diff) > z_mabp_change_critical) | (np.abs(mabp_diff) > max_mabp_change))[0]

    # plt.figure(3)
    # plt.plot(i_mabp_all, self.mabp)
    # plt.plot(i_mabp, mabp)
    ##

    ##
    i_min = np.max([np.min(i_hr), np.min(i_mabp)])
    i_max = np.min([np.max(i_hr), np.max(i_mabp)])

    i_all = np.arange(i_min, i_max+1, dtype = np.compat.long)
    self.beat_t = self.beat_t[i_all]

    if hr_interpolator is not None:
      hr_interpolator.fit(i_hr, hr)
      interval_interpolator.fit(i_hr, interval)
      self.hr = hr_interpolator.interp_fn(i_all)
      self.interval = interval_interpolator.interp_fn(i_all)
    else:
      self.hr = hr[i_all]
      self.interval = interval[i_all]

    if mabp_interpolator is not None:
      mabp_interpolator.fit(i_mabp, mabp)
      self.mabp = mabp_interpolator.interp_fn(i_all)
    else:
      self.mabp = mabp[i_all]
    ##

    self.sbp, self.dbp = self.sbp[i_all], self.dbp[i_all]

  def clip_outliers(self, num_sdevs = 2):

    def clip(x, threshold):
      return np.clip(x, x.mean() - threshold, x.mean() + threshold)

    self.sbpv = clip(self.sbpv, num_sdevs*self.sbpv.std())
    self.dbpv = clip(self.dbpv, num_sdevs*self.dbpv.std())
    self.mabpv = clip(self.mabpv, num_sdevs*self.mabpv.std())
    self.intervalv = clip(self.intervalv, num_sdevs*self.intervalv.std())
    self.hrv = clip(self.hrv, num_sdevs*self.hrv.std())

  def interpolate(self, beat_dt_new, kind='linear'):

    beat_t_new = np.arange(self.beat_t.min(), self.beat_t.max(), beat_dt_new)

    self.beat_t_original = self.beat_t

    self.sbp_original = self.sbp
    self.dbp_original = self.dbp
    self.mabp_original = self.mabp
    self.interval_original = self.interval
    self.hr_original = self.hr

    def interp(x):
      interpolator = Interpolator(kind = kind)
      interpolator.fit(self.beat_t, x)
      return interpolator.interp_fn(beat_t_new)

    # Create a new Interpolator object for each attribute
    self.sbp = interp(self.sbp)
    self.dbp = interp(self.dbp)
    self.mabp = interp(self.mabp)
    self.interval = interp(self.interval)
    self.hr = interp(self.hr)

    self.beat_t, self.beat_dt = beat_t_new, beat_dt_new

  def generate_periodogram(self, window_type = 'hann'):
    self.f_psd, self.sbp_psd = periodogram(self.sbpv, fs = 1./self.beat_dt, window = window_type)
    _, self.dbp_psd = periodogram(self.dbpv, fs =  1./self.beat_dt, window = window_type)
    _, self.mabp_psd = periodogram(self.mabpv, fs =  1./self.beat_dt, window = window_type)

    _, self.interval_psd = periodogram(self.intervalv, fs = 1./self.beat_dt, window = window_type)
    _, self.hr_psd = periodogram(self.hrv, fs = 1./self.beat_dt, window = window_type)

  def plot_realtime(self, fig_num = 1, tlim = [0, 120]):

    fig, ax = plt.subplots(2, 2, figsize=(20,10), num = fig_num)

    ax[0,0].plot(self.t, self.ecg)
    ax[0,0].plot(self.t[self.i_ecg_r], self.ecg[self.i_ecg_r], '.g')
    ax[0,0].grid()

    ax[1,0].plot(self.t, self.abp)
    ax[1,0].plot(self.t[self.i_dbp], self.abp[self.i_dbp], '.r')
    ax[1,0].plot(self.t[self.i_sbp], self.abp[self.i_sbp], '.g')
    ax[1,0].grid()
    ax[1,0].set_xlabel('Time [s]', fontsize = 20)

    ax[0,1].plot(self.t, self.ecg, label = 'ECG')
    ax[0,1].plot(self.t[self.i_ecg_r], self.ecg[self.i_ecg_r], '.g', label = 'R-peak')
    ax[0,1].legend(fontsize = 20, loc = 'upper left', bbox_to_anchor = (1.02, 1), ncol = 1)
    ax[0,1].set_xlim(tlim)
    ax[0,1].grid()

    ax[1,1].plot(self.t, self.abp, label = 'ABP')
    ax[1,1].plot(self.t[self.i_dbp], self.abp[self.i_dbp], '.r', label = 'DBP' )
    ax[1,1].plot(self.t[self.i_sbp], self.abp[self.i_sbp], '.g', label = 'SBP')
    ax[1,1].grid()
    ax[1,1].legend(fontsize = 20, loc = 'upper left', bbox_to_anchor = (1.02, 1), ncol = 1)
    ax[1,1].set_xlim(tlim)
    ax[1,1].set_xlabel('Time [s]', fontsize = 20)

    fig.tight_layout()

  def plot_beat2beat(self, fig_num = 1, tlim = [None, None], flim = [0, None]):

    fig, ax = plt.subplots(2,3, figsize=(20,10), num = fig_num)
    ax[0,0].plot(self.beat_t, self.mabp, 'b', alpha = 0.5) ;
    ax[0,0].grid()
    ax[0,0].set_ylabel('MABP', fontsize = 20) ;
    ax[0,0].set_title('Before MA Removal', fontsize = 20)

    ax[0,0].plot(self.beat_t, self.mabp_ma, 'b') ;
    ax[0,0].set_xlim(tlim)

    ax[0,1].plot(self.beat_t, self.mabpv, 'b') ;
    ax[0,1].grid()
    ax[0,1].set_title('After MA Removal', fontsize = 20)
    ax[0,1].set_xlim(tlim)

    ax[0,2].plot(self.f_psd, self.mabp_psd, 'b') ;
    ax[0,2].grid()
    ax[0,2].set_title('Power Spectrum', fontsize = 20)
    ax[0,2].set_xlim(flim)

    ax[1,0].plot(self.beat_t, self.hr, 'b', alpha = 0.5) ;
    ax[1,0].grid()
    ax[1,0].set_ylabel('HR', fontsize = 20)
    ax[1,0].plot(self.beat_t, self.hr_ma, 'b')
    ax[1,0].set_xlabel('Time [s]', fontsize = 20)
    ax[1,0].set_xlim(tlim)

    ax[1,1].plot(self.beat_t, self.hrv, 'b')
    ax[1,1].grid()
    ax[1,1].set_xlabel('Time [s]', fontsize = 20)
    ax[1,1].set_xlim(tlim)

    ax[1,2].plot(self.f_psd, self.hr_psd, 'b')
    ax[1,2].grid()
    ax[1,2].set_xlim(flim)
    ax[1,2].set_xlabel('Frequence [Hz]', fontsize = 20)

    fig.tight_layout()
