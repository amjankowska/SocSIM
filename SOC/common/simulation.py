"""Contains the base class for the simulation."""
import numpy as np
from tqdm import auto as tqdm
import numba
import matplotlib.pyplot as plt
from matplotlib import animation
import pandas
import seaborn
from . import analysis
import zarr
import datetime

class Simulation:
    """Base class for SOC simulations."""
    values = NotImplemented
    saved_snapshots = NotImplemented

    BOUNDARY_SIZE = BC = 1
    def __init__(self, L: int, save_every: int = 100): # TODO lepsze dorzucanie dodatkowych globalnych parametrów
        """__init__

        :param L: linear size of lattice, without boundary layers
        :type L: int
        :param save_every: number of iterations per snapshot save
        :type save_every: int or None
        """
        self.L = L
        self.L_with_boundary = L + 2 * self.BOUNDARY_SIZE
        self.size = L * L
        self.visited = np.zeros((self.L_with_boundary, self.L_with_boundary), dtype=bool)
        self.data_acquisition = []
        self.save_every = save_every

    def drive(self):
        """
        Drive the simulation by adding particles from the outside.

        Must be overriden in subclasses.
        """
        raise NotImplementedError("Your model needs to override the drive method!")

    def topple(self):
        """
        Distribute material from overloaded sites to neighbors.

        Must be overriden in subclasses.
        """
        raise NotImplementedError("Your model needs to override the topple method!")

    def dissipate(self):
        """
        Handle losing material at boundaries.

        This may be removed in the future.

        Must be overriden in subclasses.
        """
        pass

    @classmethod
    def clean_boundary_inplace(cls, array: np.ndarray) -> np.ndarray:
        """
        Convenience wrapper to `clean_boundary_inplace` with the simulation's boundary size. 

        :param array:
        :type array: np.ndarray
        :rtype: np.ndarray
        """
        return clean_boundary_inplace(array, self.BOUNDARY_SIZE)

    def AvalancheLoop(self) -> dict:
        """
        Bring the current simulation's state to equilibrium by repeatedly
        toppling and dissipating.

        Returns a dictionary with the total size of the avalanche
        and the number of iterations the avalanche took.

        :rtype: dict
        """
        number_of_iterations = 0 # TODO rename number_of_topples/czas rozsypywania/duration
        self.visited[...] = False
        while self.topple():
            self.dissipate()
            number_of_iterations += 1
        
        AvalancheSize = self.visited.sum()
        return dict(AvalancheSize=AvalancheSize, number_of_iterations=number_of_iterations)

    def run(self, N_iterations: int, filename: str  = None) -> dict:
        """
        Simulation loop. Drives the simulation, possibly starts avalanches, gathers data.

        :param N_iterations:
        :type N_iterations: int
        :rtype: dict
        :param filename: filename for saving snapshots. By default, something like array_Manna_2019-12-17T19:40:00.546426.zarr
        :type filename: str
        """
        if filename is None:
            filename = f"array_{self.__class__.__name__}_{datetime.datetime.now().isoformat()}.zarr"

        self.saved_snapshots = zarr.open(filename,
                                         shape=(
                                             max([N_iterations // self.save_every, 1]),
                                             self.L_with_boundary,
                                             self.L_with_boundary,
                                         ),
                                         chunks=(
                                             1,
                                             self.L_with_boundary,
                                             self.L_with_boundary,
                                         ),
                                         dtype=self.values.dtype,
                                         )

        for i in tqdm.trange(N_iterations):
            self.drive()
            observables = self.AvalancheLoop()
            self.data_acquisition.append(observables)
            if self.save_every is not None and (i % self.save_every) == 0:
                self._save_snapshot(i)

    def _save_snapshot(self, i):
        self.saved_snapshots[i // self.save_every] = self.values

    def plot_histogram(self, column='AvalancheSize', num=50, filename = None, plot = True):
        df = pandas.DataFrame(self.data_acquisition)
        fig, ax = plt.subplots()
        min_range = np.log10(df[column].min()+1)
        bins = np.logspace(min_range,
                           np.log10(df[column].max()+1),
                           num = num)
        heights, bins, _ = ax.hist(df[column], bins)
        ax.set_yscale('log')
        ax.set_xscale('log')
        ax.set_xlabel(column)
        ax.set_ylabel("count")
        if filename is not None:
            fig.savefig(filename)
        plt.tight_layout()
        if plot:
            plt.show()
        else:
            plt.close()
        return heights, bins

    def plot_state(self, with_boundaries = False):
        """
        Plots the current state of the simulation.
        """
        fig, ax = plt.subplots()

        if with_boundaries:
            values = self.values
        else:
            values = self.values[self.BOUNDARY_SIZE:-self.BOUNDARY_SIZE, self.BOUNDARY_SIZE:-self.BOUNDARY_SIZE]
        
        IM = ax.imshow(values, interpolation='nearest')
        
        plt.colorbar(IM)
        return fig

    def animate_states(self, notebook: bool = False, with_boundaries: bool = False):
        """
        Animates the collected states of the simulation.

        :param notebook: if True, displays via html5 video in a notebook;
                        otherwise returns MPL animation
        :type notebook: bool
        :param with_boundaries: include boundaries in the animation?
        :type with_boundaries: bool
        """
        fig, ax = plt.subplots()

        if with_boundaries:
            values = np.dstack(self.saved_snapshots)
        else:
            values = np.dstack(self.saved_snapshots)[self.BOUNDARY_SIZE:-self.BOUNDARY_SIZE, self.BOUNDARY_SIZE:-self.BOUNDARY_SIZE, :]

        IM = ax.imshow(values[:, :, 0],
                       interpolation='nearest',
                       vmin = values.min(),
                       vmax = values.max()
                       )
        
        plt.colorbar(IM)
        iterations = values.shape[2]
        title = ax.set_title("Iteration {}/{}".format(0, iterations * self.save_every))

        def animate(i):
            IM.set_data(values[:,:,i])
            title.set_text("Iteration {}/{}".format(i * self.save_every, iterations * self.save_every))
            return IM, title

        anim = animation.FuncAnimation(fig,
                                       animate,
                                       frames=iterations,
                                       interval=30,
                                       )
        if notebook:
            from IPython.display import HTML, display
            plt.close(anim._fig)
            display(HTML(anim.to_html5_video()))
        else:
            return anim
    
    def get_exponent(self, *args, **kwargs):
        return analysis.get_exponent(self, *args, **kwargs)

        
@numba.njit
def clean_boundary_inplace(array: np.ndarray, boundary_size: int, fill_value = False) -> np.ndarray:
    """
    Fill `array` at the boundary with `fill_value`.

    Useful to make sure sites on the borders do not become active and don't start toppling.

    Works inplace - will modify the existing array!

    :param array:
    :type array: np.ndarray
    :param boundary_size:
    :type boundary_size: int
    :param fill_value:
    :rtype: np.ndarray
    """
    array[:boundary_size, :] = fill_value
    array[-boundary_size:, :] = fill_value
    array[:, :boundary_size] = fill_value
    array[:, -boundary_size:] = fill_value
    return array

