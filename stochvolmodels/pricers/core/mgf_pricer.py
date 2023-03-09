"""
generic analytics for option pricing using Fourier transforms of payoffs
"""

import numpy as np
from numba import njit
from typing import Tuple

from ...pricers.core.config import VariableType


@njit(cache=False, fastmath=True)
def get_phi_grid(is_spot_measure: bool = True,
                 vol_scaler: float = 0.28
                 ) -> np.ndarray:
    """
    for x = log-price variable
    vol_scaler = sigma_0*sqrt(ttm) will adjust the grid size: smaller val need longer period
    default vol scaler corresponds to pricing option with vol=100% and 1m ttm = 1/12
    """
    p = np.linspace(0, 5.6/vol_scaler, 1000)  # default max phi is 20 with 1000 points
    real_p = -0.5 if is_spot_measure else 0.5
    return real_p + 1j * p


@njit(cache=False, fastmath=True)
def get_psi_grid() -> np.ndarray:
    """
    for I = QV variable
    """
    p = np.linspace(0, 200, 4000)
    real_p = -0.5
    return real_p + 1j * p


@njit(cache=False, fastmath=True)
def get_theta_grid() -> np.ndarray:
    """
    for sigma
    """
    p = np.linspace(0, 600, 4000)
    real_p = -0.5
    return real_p + 1j * p


@njit(cache=False, fastmath=True)
def get_transform_var_grid(variable_type: VariableType = VariableType.LOG_RETURN,
                           is_spot_measure: bool = True,
                           vol_scaler: float = 0.28
                           ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    compute grid for Fourier inversions
    """
    if variable_type == VariableType.LOG_RETURN:
        phi_grid = get_phi_grid(is_spot_measure=is_spot_measure, vol_scaler=vol_scaler)
        psi_grid = np.zeros_like(phi_grid, dtype=np.complex128)
        theta_grid = np.zeros_like(phi_grid, dtype=np.complex128)

    elif variable_type == VariableType.Q_VAR:
        psi_grid = get_psi_grid()
        if is_spot_measure:
            phi_grid = np.zeros_like(psi_grid, dtype=np.complex128)
        else:
            phi_grid = np.ones_like(psi_grid, dtype=np.complex128)
        theta_grid = np.zeros_like(phi_grid, dtype=np.complex128)

    elif variable_type == VariableType.SIGMA:
        theta_grid = get_theta_grid()
        phi_grid = np.zeros_like(theta_grid, dtype=np.complex128)
        psi_grid = np.zeros_like(theta_grid, dtype=np.complex128)

    else:
        raise NotImplementedError
    return phi_grid, psi_grid, theta_grid


@njit(cache=False, fastmath=True)
def compute_integration_weights(var_grid: np.ndarray,
                                is_simpson: bool = True
                                ) -> np.ndarray:
    p = np.imag(var_grid)
    if is_simpson:
        dp = 2.0*np.ones(len(p))
        dp[0] = 1.0
        dp[-1] = 1.0
        for i in range(len(p)):
            if i % 2 == 1:
                dp[i] = 4.0
        dp = ((p[1] - p[0])/3.0) * dp
    else:  # trapezoidal rule
        dp = np.append(0.5*(p[1] - p[0]), p[1:] - p[:-1])
    return dp


@njit(cache=False, fastmath=True)
def slice_pricer_with_mgf_grid(log_mgf_grid: np.ndarray,
                               phi_grid: np.ndarray,
                               ttm: float,
                               forward: float,
                               strikes: np.ndarray,
                               optiontypes: np.ndarray,
                               discfactor: float = 1.0,
                               is_spot_measure: bool = True,
                               is_simpson: bool = True
                               ) -> np.ndarray:
    """
    generic function for pricing options on the spot given the mgf grid
    mgf in x is function defined on log-price transform phi grids
    transform variable is phi_grid = real_phi + i*p
    grid can be non-uniform
    """
    p = np.imag(phi_grid)
    dp = compute_integration_weights(var_grid=phi_grid, is_simpson=is_simpson)

    if np.all(np.abs(np.real(phi_grid)) < 0.5000000001):  # optimized for phi = +/-0.5 + i*p
        p_payoff = (dp / np.pi) / (p * p + 0.25) + 1j * 0.0  # add zero complex part for numba
    elif is_spot_measure:
        p_payoff = - (dp / np.pi) / ((phi_grid+1.0)*phi_grid)
    else:
        p_payoff = - (dp / np.pi) / ((phi_grid-1.0) * phi_grid)

    log_strikes = np.log(forward/strikes)
    option_prices = np.zeros_like(log_strikes)
    for idx, (x, strike, type_) in enumerate(zip(log_strikes, strikes, optiontypes)):
        # compute sum using trapesoidal rule
        capped_option_price = np.nansum(np.real(p_payoff*np.exp(-x * phi_grid + log_mgf_grid)))
        if is_spot_measure:
            if type_ == 'C':
                option_prices[idx] = discfactor*(forward - strike * capped_option_price)
            elif type_ == 'P':
                option_prices[idx] = discfactor*(strike - strike * capped_option_price)
            else:
                raise ValueError("not implemented")
        elif type_ in ['IC', 'C']:
            option_prices[idx] = forward*discfactor*(1.0 - capped_option_price)
        elif type_ in ['IP', 'P']:
            option_prices[idx] = forward*discfactor*(np.exp(-x) - capped_option_price)
        else:
            raise ValueError("not implemented")

    return option_prices


@njit(cache=False, fastmath=True)
def slice_qvar_pricer_with_a_grid(log_mgf_grid: np.ndarray,
                                  psi_grid: np.ndarray,
                                  ttm: float,
                                  strikes: np.ndarray,
                                  optiontypes: np.ndarray,
                                  forward: float,
                                  discfactor: float = 1.0,
                                  is_simpson: bool = True,
                                  is_spot_measure: bool = True
                                  ) -> np.ndarray:
    """
    generic pricer of options on quadratic var using mgf
    mmg in x as function of a and phi grids
    ode_solution is computed per grid of phi
    """
    option_prices = np.zeros_like(strikes)
    log_strikes = np.log(forward / strikes)
    dp = compute_integration_weights(var_grid=psi_grid, is_simpson=is_simpson)
    p_payoff = (dp / np.pi) / (psi_grid * psi_grid)
    for idx, (log_strike, strike, type_) in enumerate(zip(log_strikes, strikes, optiontypes)):
        option_price = np.nansum(np.real(p_payoff * np.exp((strike * ttm) * psi_grid + log_mgf_grid)))
        option_price = np.maximum(discfactor * option_price / ttm, 1e-10)

        if type_ == 'C':
            option_prices[idx] = option_price
        else:
            raise ValueError("not implemented")
    return option_prices


@njit(cache=False, fastmath=True)
def pdf_with_mgf_grid(log_mgf_grid: np.ndarray,
                      transform_var_grid: np.ndarray,
                      space_grid: np.ndarray,
                      shift: float = 0.0,
                      is_simpson: bool = True
                      ) -> np.ndarray:
    """
    generic function for pricing options on the spot given the mgf grid
    mgf in x is function defined on log-price transform phi grids
    transform variable is phi_grid = real_phi + i*p
    grid can be non-uniform
    """
    dp = compute_integration_weights(var_grid=transform_var_grid, is_simpson=is_simpson) / np.pi
    pdf = np.zeros_like(space_grid)
    for idx, x in enumerate(space_grid):
        pdf[idx] = np.nansum(np.real(dp * np.exp((x-shift) * transform_var_grid + log_mgf_grid)))
    dx = space_grid[1] - space_grid[0]
    pdf = dx * pdf
    return pdf
