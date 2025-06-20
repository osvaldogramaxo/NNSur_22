import gwsurrogate
import torch
import os
import warnings
import gwtools as _gwtools
import numpy as np
from gwtools import gwutils as _gwutils
from gwsurrogate import SurrogateEvaluator
class DANSur(SurrogateEvaluator):
    
    def __init__(self, modes_list = [(2, 2)], device='cpu'):
        """
        gwsurrogate wrapper for the NRSurNN3dq8. Intrinsic parameters are mass ratio (q) and spins (chiA0, chiB0).  
        This model accepts batch inputs for the input parameters, so q can be a float or np.ndarray of shape (N,), where N is the batch size.
        Since this model is non-precessing, only the z-components of the spins should exist. As such, while chiA0 and chiB0 can be of the type [0., 0., chiz],
        following gwsurrogate convention, the user can pass only the z-components of the spins as a float or np.ndarray of shape (N,). The output is a dictionary
        of the waveform modes, where the keys are the mode tuple (l, m) and the values are the complex numpy arrays of the waveform modes, of shape (N, 2048).
        """
        self.name = 'DANSur'

        # load the dimensionless surrogate
        self._sur_dimless = self._load_dimless_surrogate()

        self._domain_type = 'Time'
        if self._domain_type not in ['Time', 'Frequency']:
            raise Exception('Invalid domain_type.')

        # Get some useful keywords, set missing keywords to default values
        self.keywords = {}
        self._check_keywords_and_set_defaults()

        self.soft_param_lims = [8,0.8]
        self.hard_param_lims = [10,0.8]

        print('Loaded %s model'%self.name)

        
        self.modes_list = modes_list
        self.device = device
    def _load_dimless_surrogate(self):
        """
        This function, which must be overriden for each derived class of
        SurrogateEvaluator, handles the loading of the dimensionless surrogate.
        This should return the loaded surrogate.
        The loaded surrogate should have a __call__ function that returns the
        dimensionless time/frequency array and dimensionless waveform modes.
        The return value of this functions will be stored as
        self._sur_dimless()
        The __call__ function of self._sur_dimless() should take all inputs
        passed to self._sur_dimless() in the __call__ function of this class.
        See NRHybSur3dq8 for an example.
        """
        
        DANSurModel = torch.jit.load(os.path.dirname(__file__)+'/DANSur.pt', map_location='cpu')
        return DANSurModel
    def _get_intrinsic_parameters(self, q, chiA0, chiB0, precessing_opts,
            tidal_opts, par_dict):
        """
        This function, which must be overriden for each derived class of
        SurrogateEvaluator, puts all intrinsic parameters of the surrogate
        into a single array.
        For example:
            For NRHybSur3dq8: x = [q, chiA0, chiB0].
            For NRSur7dq4: x = [q, chiA, chiB], where chiA/chiB are vectors of
                size 3.
        """
        # check inputs are torch tensors. If not, convert them
        q_in = q if q<=1. else 1./q
        if not isinstance(q, torch.Tensor):
            q = torch.tensor(q)
            q_in = torch.tensor(q_in)
        if not isinstance(chiA0, torch.Tensor):
            chiA0 = torch.tensor(chiA0)
        if not isinstance(chiB0, torch.Tensor):
            chiB0 = torch.tensor(chiB0)
        # check for batch dimension. If not present, add it
        
        # if len(q.shape) == 1:
        #     q = q.unsqueeze(-1)
        if (chiA0).shape[-1] != 1:
            chiA0 = (chiA0)[...,2].unsqueeze(0)
        if (chiB0).shape[-1] != 1:
            chiB0 = (chiB0)[...,2].unsqueeze(0)
        # print(q, chiA0, chiB0)
        # print(chiA0.shape,chiB0.shape)

        torch_inputs = torch.stack((q_in, chiA0, chiB0)).float().T
        return torch_inputs
    def _check_params(self, q, chiA0, chiB0, precessing_opts, tidal_opts,
            par_dict):
        """ Checks that the parameters are valid.

            Raises a warning if outside self.soft_param_lims and
            raises an error if outside self.hard_param_lims.
            If these are None, skips the checks.

            Also some sanity checks for precessing and tidal models.
        """
        ## Allow violations within this value.
        # Sometimes, chi can be 1+1e-16 due to machine precision limitations,
        # this will ignore such cases
        grace = 1e-14

        chiAmag = torch.linalg.norm(chiA0, dim=-1) if len(chiA0.shape)!=1 else chiA0
        chiBmag = torch.linalg.norm(chiB0, dim=-1) if len(chiB0.shape)!=1 else chiB0

        if not self.keywords['Precessing']:
            if len(chiA0.shape)==1:
                assert len(chiB0.shape)==1
            else:
                if (torch.linalg.norm(chiA0[..., :2]) > grace
                        or torch.linalg.norm(chiB0[..., :2]) > grace):
                    raise Exception('Got precessing spins for a nonprecessing '
                        'model')

                if precessing_opts is not None:
                    raise Exception('precessing_opts should be None for '
                            'nonprecessing models')


        if self.keywords['Tidal']:
            if (tidal_opts is None) or (('Lambda1' not in tidal_opts.keys())
                    or ('Lambda2' not in tidal_opts.keys())):
                raise Exception('Tidal parameters Lambda1 and Lambda2 should '
                        'be passed through tidal_opts for this model.')
        else:
            if tidal_opts is not None:
                raise Exception('tidal_opts should be None for nontidal '
                        'models')


        # Extrapolation checks
        if self.hard_param_lims is not None:
            qMax = self.hard_param_lims[0]
            chiMax = self.hard_param_lims[1]
            if (q > qMax + grace).any() or (q < 0.99).any():
                # print(q.shape)
                raise Exception(f'Mass ratio q={q} is outside allowed '
                    f'range: 1<=q<={qMax}')
            # print(chiAmag, chiAmag.shape)
            if (chiAmag > chiMax + grace).any():
                raise Exception(f'Spin magnitude of BhA={chiAmag} is outside '
                    f'allowed range: chi<={chiMax}')
            if (chiBmag > chiMax + grace).any():
                raise Exception(f'Spin magnitude of BhB={chiBmag} is outside '
                    f'allowed range: chi<={chiMax}')

        if self.soft_param_lims is not None:
            qMax = self.soft_param_lims[0]
            chiMax = self.soft_param_lims[1]
            if (q > qMax).any():
                
                warnings.warn('Mass ratio q=%.4f is outside training '
                    'range: 1<=q<=%.4f'%(q.max(), qMax))
            if (chiAmag > chiMax).any():
                warnings.warn('Spin magnitude of BhA=%.4f is outside '
                    'training range: chi<=%.4f'%(chiAmag, chiMax))
            if (chiBmag > chiMax).any():
                warnings.warn('Spin magnitude of BhB=%.4f is outside '
                    'training range: chi<=%.4f'%(chiBmag, chiMax))
    # @torch.compile(mode='reduce-overhead', fullgraph=True, dynamic=True)
    @staticmethod
    def to_wave(x):
        wav = x[...,:2048]*torch.exp(1j*x[...,2048:])
        # wav = torch.roll(wav, 50, dims=-1)
        return wav
    @torch.inference_mode()
    def __call__(self, q, chiA0, chiB0, M=None, dist_mpc=None, f_low=0,
        f_ref=None, dt=None, df=None, times=None, freqs=None,
        mode_list=None, ellMax=None, inclination=None, phi_ref=0,
        precessing_opts=None, tidal_opts=None, par_dict=None,
        units='dimensionless', skip_param_checks=False,
        taper_end_duration=None):
        """
    INPUT
    =====
    q :         Mass ratio, mA/mB >= 1.
    chiA0:      Dimensionless spin vector of the heavier black hole at
                reference epoch.
    chiB0:      Dimensionless spin vector of the lighter black hole at
                reference epoch.

                This follows the same convention as LAL, where the spin
                components are defined as:
                \chi_z = \chi \cdot \hat{L}, where L is the orbital angular
                    momentum vector at the epoch.
                \chi_x = \chi \cdot \hat{n}, where n = body2 -> body1 is the
                    separation vector at the epoch. body1 is the heavier body.
                \chi_y = \chi \cdot \hat{L \cross n}.
                These spin components are frame-independent as they are
                defined using vector inner products. This is equivalent to
                specifying the spins in the coorbital frame used in the
                surrogate papers.

    M, dist_mpc: Either specify both M and dist_mpc or neither.
        M        :  Total mass (solar masses). Default: None.
        dist_mpc :  Distance to binary system (MegaParsecs). Default: None.

    f_low :     Instantaneous initial frequency of the (2, 2) mode. In
                practice, this is estimated to be twice the initial orbital
                frequency in the coprecessing frame. Note: the coprecessing
                frame is the minimal rotation frame of arXiv:1110.2965.

                f_low should be in cycles/M if units = 'dimensionless',
                should be in Hertz if units = 'mks'.
                If 0, the entire waveform is returned.
                Default: None, must be specified by user.

                NOTE: For some models like NRSur7dq4, f_low=0 is recommended.
                The role of f_low is only to truncate the lower frequencies
                before returning the waveform. Since this model is already
                very short, this truncation is not required. On the other hand,
                f_ref is used to set the reference epoch, and can be freely
                specified.

                WARNING: Using f_low=0 with a small dt (like 0.1M) can lead to
                very expensive evaluation for hybridized surrogates like
                NRHybSur3dq8.

    f_ref:      Frequency used to set the reference epoch at which the
                reference frame is defined and the spins are specified.
                See below for definition of the reference frame.
                Should be in cycles/M if units = 'dimensionless', should be
                in Hertz if units = 'mks'.
                Default: If f_ref is not given, we set f_ref = f_low. If
                f_low is 0, this corresponds to the initial index.

                For time domain models, f_ref is used to determine a t_ref,
                such that the orbital frequency in the coprecessing frame
                equals f_ref/2 at t=t_ref.

    dt, df :    Time/Frequency step size, specify at most one of dt/df,
                depending on whether the surrogate is a time/frequency domain
                surrogate.
                Default: None. If None, the internal domain of the surrogate is
                used, which can be nonuniformly sampled.
                dt (df) Should be in M (cycles/M) if units = 'dimensionless',
                should be in seconds (Hertz) if units = 'mks'. Do not specify
                times/freqs if using dt/df.


    times, freqs:
                Array of time/frequency samples at which to evaluate the
                waveform, depending on whether the surrogate is a
                time/frequency domain surrogate. time (freqs) should be in
                M (cycles/M) if units = 'dimensionless', should be in
                seconds (Hertz) if units = 'mks'. Do not specify dt/df if
                using times/freqs. Default None.

    ellMax:     Maximum ell index for modes to include. All available m
                indicies for each ell will be included automatically. The 
                m<0 modes will automatically be included for nonprecessing
                models. 
                Default: None, in which case all available ells will be
                included. 

    mode_list : A list of (ell, m) modes tuples to be included. Valid only
                for nonprecessing models.

                Example: mode_list = [(2,2),(2,1)].
                Default: None, in which case all available modes are included.

                At most one of ellMax and mode_list can be specified.

                Note: mode_list is allowed only for nonprecessing models; for
                precessing models use ellMax. For precessing systems, all m
                indices of a given ell index mix with each other, so there is
                no clear hierarchy. To get the individual modes just don't
                specify inclination and a dictionary of modes will be returned.

                Note: When the inclination is set, the m<0 modes are 
                automatically included. For example, passing mode_list = [(2,2)] 
                will include the (2,2) and (2,-2) modes in the computation of
                the strain.

                Note: When the inclination is None, the m<0 modes are
                automatically generated.

    inclination : Inclination angle between the orbital angular momentum
                direction at the reference epoch and the line-of-sight to the
                observer. If inclination is None, the mode data is returned
                as a dictionary.
                Default: None.

    phi_ref :   The azimuthal angle on the sky of the source frame following
                the LAL convention.
                Default: 0.

                If inclination/phi_ref are specified, the complex strain (h =
                hplus -i hcross) evaluated at (inclination, pi/2 - phi_ref) on
                the sky of the reference frame is returned. This follows the
                same convention as LAL. See below for definition of the
                reference frame.

    precessing_opts:
                A dictionary containing optional parameters for a precessing
                surrogate model. Default: None.
                Allowed keys are:
                init_orbphase: The orbital phase in the coprecessing frame
                    at the reference epoch.
                    Default: 0, in which case the coorbital frame and
                    coprecessing frame are the same.
                init_quat: The unit quaternion (length 4 vector) giving the
                    rotation from the coprecessing frame to the inertial frame
                    at the reference epoch.
                    Default: None, in which case the coprecessing frame is the
                    same as the inertial frame.
                return_dynamics:
                    Return the frame dynamics and spin evolution along with
                    the waveform. Default: False.
                Example: precessing_opts = {
                                    'init_orbphase': 0,
                                    'init_quat': [1,0,0,0],
                                    'return_dynamics': True
                                    }

    tidal_opts:
                A dictionary containing optional parameters for a tidal
                surrogate model. Default: None.
                Allowed keys are:
                Lambda1: The tidal deformability parameter for the heavier
                    object.
                Lambda2: The tidal deformability parameter for the lighter
                    object.
                Example: tidal_opts = {'Lambda1': 200, 'Lambda2': 300}


    par_dict:   A dictionary containing any additional parameters needed for a
                particular surrogate model. Default: None.

    units:      'dimensionless' or 'mks'. Default: 'dimensionless'.
                If 'dimensionless': Any of f_low, f_ref, dt, df, times and
                    freqs, if specified, must be in dimensionless units. That
                    is, dt/times should be in units of M, while f_ref, f_low
                    and df/freqs should be in units of cycles/M.
                    M and dist_mpc must be None. The waveform and domain are
                    returned as dimensionless quantities as well.
                If 'mks': Any of f_low, f_ref, dt, df, times and freqs, if
                    specified, must be in MKS units. That is, dt/times should
                    be in seconds, while f_ref, f_low and df/freqs should be
                    in Hz. M and dist_mpc must be specified. The waveform and
                    domain are returned in MKS units as well.


    skip_param_checks :
                Skip sanity checks for inputs. Use this if you want to
                extrapolate outside allowed range. Default: False.

    taper_end_duration:
                Taper the last TAPER_END_DURATION (M) of a time-domain waveform
                in units of M. For exmple, passing 40 will taper the last 40M.
                When set to None, no taper is applied
                Default: None.

    RETURNS
    =====

    domain, h, dynamics


    domain :    Array of time/frequency samples corresponding to h and
                dynamics, depending on whether the surrogate is a
                time/frequency domain model. This is the same as times/freqs
                if times/freqs are given as an inputs.
                For time domain models the time is set to 0 at the peak of
                the waveform. The time (frequency) values are in M (cycles/M)
                if units = 'dimensionless', they are in seconds (Hertz) if
                units = 'mks'

    h :         The waveform.
                    If inclination is specified, the complex strain (h = hplus
                    -i hcross) evaluated at (inclination, pi/2 - phi_ref) on
                    the sky of the reference frame is returned. This follows
                    the LAL convention, see below for details.  This includes
                    all modes given in the ellMax/mode_list argument. For
                    nonprecessing systems the m<0 modes are automatically
                    deduced from the m>0 modes. To see if a model is precessing
                    check self.keywords.

                    Else, h is a dictionary of available modes with (l, m)
                    tuples as keys. For example, h22 = h[(2,2)].

                    If M and dist_mpc are given, the physical waveform
                    at that distance is returned. Else, it is returned in
                    code units: r*h/M extrapolated to future null-infinity.

    dynamics:   A dict containing the frame dynamics and spin evolution. This
                is None for nonprecessing models. This is also None if
                return_dynamics in precessing_opts is False (Default).

                The dynamics include (L=len(domain)):

                q_copr = dynamics['q_copr']
                    The quaternion representing the coprecessing frame with
                    shape (4, L)
                orbphase = dynamics['orbphase']
                    The orbital phase in the coprecessing frame with length L.
                chiA = dynamics['chiA']
                    The inertial frame chiA with shape (L, 3)
                chiB = dynamics['chiB']
                    The inertial frame chiB with shape (L, 3)


    IMPORTANT NOTES:
    ===============

    The reference frame (or inertial frame) is defined as follows:
        The +ve z-axis is along the orbital angular momentum at the reference
        epoch. The separation vector from the lighter BH to the heavier BH at
        the reference epoch is along the +ve x-axis. The y-axis completes the
        right-handed triad. The reference epoch is set using f_ref.

        Now, if inclination/phi_ref are given, the waveform is evaluated at
        (inclination, pi/2 - phi_ref) in the reference frame. This agrees with
        the LAL convention. See LIGO DCC document T1800226 for the LAL frame
        diagram.
        """

        if not isinstance(q, torch.Tensor):
            q = torch.tensor(q)
            if len(q.shape) == 0:
                q = q.unsqueeze(0)
        if not isinstance(chiA0, torch.Tensor):
            chiA0 = torch.tensor(chiA0)
            if len(chiA0.shape) == 0:
                chiA0 = chiA0.unsqueeze(0)
        if not isinstance(chiB0, torch.Tensor):
            chiB0 = torch.tensor(chiB0)
            if len(chiB0.shape) == 0:
                chiB0 = chiB0.unsqueeze(0)
        if q<=1: q = 1./q

        # Sanity checks
        if not skip_param_checks:

            if (M is None) ^ (dist_mpc is None):
                raise ValueError("Either specify both M and dist_mpc, or "
                        "neither")

            if (M is not None) ^ (units == 'mks'):
                raise ValueError("M/dist_mpc must be specified if and only if"
                    " units='mks'")

            if (dt is not None) and (self._domain_type != 'Time'):
                raise ValueError("%s is not a Time domain model, cannot "
                        "specify dt"%self.name)

            if (times is not None) and (self._domain_type != 'Time'):
                raise ValueError("%s is not a Time domain model, cannot "
                        "specify times"%self.name)

            if (df is not None) and (self._domain_type != 'Frequency'):
                raise ValueError("%s is not a Frequency domain model, cannot"
                    " specify df"%self.name)

            if (freqs is not None) and (self._domain_type != 'Frequency'):
                raise ValueError("%s is not a Frequency domain model, cannot"
                    " specify freqs"%self.name)

            if (dt is not None) and (times is not None):
                raise ValueError("Cannot specify both dt and times.")

            if (df is not None) and (freqs is not None):
                raise ValueError("Cannot specify both df and freqs.")

            if (f_low is None):
                raise ValueError("f_low must be specified.")

            if (f_ref is not None) and (f_ref < f_low):
                raise ValueError("f_ref cannot be lower than f_low.")

            if (mode_list is not None) and (ellMax is not None):
                raise ValueError("Cannot specify both mode_list and ellMax.")

            if (mode_list is not None) and self.keywords['Precessing']:
                raise ValueError("mode_list is not allowed for precessing "
                        "models, use ellMax instead.")

            if (taper_end_duration is not None) and self._domain_type !='Time':
                raise ValueError("%s is not a Time domain model, cannot taper")

            # more sanity checks including extrapolation checks
            self._check_params(q, chiA0, chiB0, precessing_opts, tidal_opts,
                    par_dict)


        x = self._get_intrinsic_parameters(q, chiA0, chiB0, precessing_opts,
            tidal_opts, par_dict)
        # print(x)

        # Get scalings from dimensionless units to mks units
        if units == 'dimensionless':
            amp_scale = np.ones_like(q)
            t_scale = np.ones_like(q)
        elif units == 'mks':
            amp_scale = \
                M*_gwtools.Msuninsec*_gwtools.c/(1e6*dist_mpc*_gwtools.PC_SI)
            t_scale = _gwtools.Msuninsec * M
        else:
            raise Exception('Invalid units')

        # If f_ref is not given, we set it to f_low.
        if f_ref is None:
            f_ref = f_low

        # Get dimensionless step size or times/freqs and reference time/freq
        dtM = None if dt is None else dt/t_scale
        timesM = None if times is None else times/t_scale
        dfM = None if df is None else df*t_scale
        freqsM = None if freqs is None else freqs*t_scale


        # Get waveform modes and domain in dimensionless units
        fM_low = f_low*t_scale
        fM_ref = f_ref*t_scale
        # print(x)
        # print(x.shape)
        h = self._sur_dimless(x)
        h = self.to_wave(h).numpy()
        h = {tuple(mode): h[i] for i, mode in enumerate(self.modes_list)}
        # taper the last portion of the waveform, regardless of whether or not
        # this corresponds to inspiral, merger, or ringdown.
        if taper_end_duration is not None:
            h_tapered = {}
            for mode, hlm in h.items():
                # NOTE: we use a roll on window [domain[0]-100, domain[0]-50]
                # to trick the window function into not tapering the beginning
                # of h
                h_tapered[mode] = _gwutils.windowWaveform(domain, hlm, \
                    domain[0]-100, domain[0]-50, \
                    domain[-1] - taper_end_duration, domain[-1], \
                    windowType="planck")

            h = h_tapered

        # sum over modes to get complex strain if inclination is given
        if inclination is not None:
            # For nonprecessing systems get the m<0 modes from the m>0 modes.
            fake_neg_modes = not self.keywords['Precessing']

            # Follows the LAL convention (see help text)
            h = self._mode_sum(h, inclination, np.pi/2 - phi_ref,
                    fake_neg_modes=fake_neg_modes)
        else: # if returning modes, check if m<0 modes need to be generated for nonprecessing systems
            if not self.keywords['Precessing']:
                modes = list(h.keys())
                for mode in modes:
                    ell = mode[0]
                    m   = mode[1]
                    if (m > 0) and ( (ell,-m) not in h.keys()):
                        h[(ell,-m)] = (-1)**ell * h[(ell,m)].conj()

        # Rescale domain to physical units
        # print(h)
        domain = np.arange(-4096+100, 100, 2).astype(float)#*np.ones_like(h.real)
        # domain
        # print(domain.shape, t_scale.shape)
        # print('Pre-scale', domain[0], domain[-1], self._domain_type)
        # print(domain)
        # print(M, t_scale)
        if self._domain_type == 'Time':
            domain *= t_scale
        elif self._domain_type == 'Frequency':
            print('WHAT')
            domain /= t_scale
        else:
            raise Exception('Invalid _domain_type.')
        # print('Post-scale', domain[0], domain[-1])
        domain = domain - domain[0] 
        domain = domain.astype(float)
        
        # interpd_h = {}
        # for mode, hlm in h.items():
        #     if self._domain_type == 'Time':
        if units=='mks':
            dt_t = times[1] - times[0]
        else: dt_t = domain[1]
        # print(domain[0], domain[-1]+dt_t, 1/dt_t)
        interp_domain = np.arange(domain[0], domain[-1]+dt_t, dt_t).astype(float)
        

        # Rescale waveform to physical units
        
        if np.array(amp_scale != 1).any():
            if type(h) == dict:
                h.update((x, y*amp_scale[:,None]) for (x,y) in h.items())
            else:
                h *= amp_scale
        dynamics = None
        if units == 'mks':
            interp_h = np.interp(interp_domain, domain , h)[-len(times):]
            if len(interp_h) < len(times):
                interp_h = np.concatenate((np.zeros(len(times)-len(interp_h)), interp_h))
        else:
            # print(h) 
            interp_h = h[(2,2)]
        rolltarg = int( len(interp_h)-np.argmax(abs(interp_h)) )
        interp_h = np.roll(interp_h, rolltarg, -1)
        
        return interp_domain, interp_h, dynamics