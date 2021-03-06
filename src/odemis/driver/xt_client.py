# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This file is part of Odemis.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see
http://www.gnu.org/licenses/.
"""
from __future__ import division, print_function

import logging
import threading
import time
from concurrent.futures import CancelledError

import Pyro5.api
import msgpack_numpy
import numpy

from odemis import model
from odemis import util
from odemis.model import CancellableThreadPoolExecutor, HwError, isasync, CancellableFuture, ProgressiveFuture

Pyro5.api.config.SERIALIZER = 'msgpack'
msgpack_numpy.patch()

XT_RUN = "run"
XT_STOP = "stop"

# Convert from a detector role (following the Odemis convention) to a detector name in xtlib
DETECTOR2CHANNELNAME = {
    "se-detector": "electron1",
}

class SEM(model.HwComponent):
    """
    Driver to communicate with XT software on TFS microscopes. XT is the software TFS uses to control their microscopes.
    To use this driver the XT adapter developed by Delmic should be running on the TFS PC. Communication to the
    Microscope server is done via Pyro5.
    """

    def __init__(self, name, role, children, address, daemon=None,
                 **kwargs):
        """
        Parameters
        ----------
        address: str
            server address and port of the Microscope server, e.g. "PYRO:Microscope@localhost:4242"
        timeout: float
            Time in seconds the client should wait for a response from the server.
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(address)
            self.server._pyroTimeout = 30  # seconds
            self._swVersion = self.server.get_software_version()
            self._hwVersion = self.server.get_hardware_version()
        except Exception as err:
            raise HwError("Failed to connect to XT server '%s'. Check that the "
                          "uri is correct and XT server is"
                          " connected to the network. %s" % (address, err))

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # create a focuser, if requested
        if "focus" in children:
            ckwargs = children["focus"]
            self._focus = Focus(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._focus)

    def list_available_channels(self):
        """
        List all available channels and their current state as a dict.

        Returns
        -------
        available channels: dict
            A dict of the names of the available channels as keys and the corresponding channel state as values.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.list_available_channels()

    def move_stage(self, position, rel=False):
        """
        Move the stage the given position in meters. This is non-blocking. Throws an error when the requested position
        is out of range.

        Parameters
        ----------
        position: dict(string->float)
            Absolute or relative position to move the stage to per axes in m. Axes are 'x' and 'y'.
        rel: boolean
            If True the staged is moved relative to the current position of the stage, by the distance specified in
            position. If False the stage is moved to the absolute position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage(position, rel)

    def stage_is_moving(self):
        """Returns: (bool) True if the stage is moving and False if the stage is not moving."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_is_moving()

    def stop_stage_movement(self):
        """Stop the movement of the stage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_stage_movement()

    def get_stage_position(self):
        """
        Returns: (dict) the axes of the stage as keys with their corresponding position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_position()

    def stage_info(self):
        """Returns: (dict) the unit and range of the stage position."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_info()

    def get_latest_image(self, channel_name):
        """
        Acquire an image observed via the currently set channel. Note: the channel needs to be stopped before an image
        can be acquired. To acquire multiple consecutive images the channel needs to be started and stopped. This
        causes the acquisition speed to be approximately 1 fps.

        Returns
        -------
        image: numpy array
            The acquired image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            image = self.server.get_latest_image(channel_name)
            return image

    def set_scan_mode(self, mode):
        """
        Set the scan mode.
        Parameters
        ----------
        mode: str
            Name of desired scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_mode(mode)

    def get_scan_mode(self):
        """
        Get the scan mode.
        Returns
        -------
        mode: str
            Name of set scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_mode()

    def set_selected_area(self, start_position, size):
        """
        Specify a selected area in the scan field area.

        Parameters
        ----------
        start_position: (tuple of int)
            (x, y) of where the area starts in pixel, (0,0) is at the top left.
        size: (tuple of int)
            (width, height) of the size in pixel.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_selected_area(start_position, size)

    def get_selected_area(self):
        """
        Returns
        -------
        x, y, width, height: pixels
            The current selected area. If selected area is not active it returns the stored selected area.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            x, y, width, height = self.server.get_selected_area()
            return x, y, width, height

    def selected_area_info(self):
        """Returns: (dict) the unit and range of set selected area."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.selected_area_info()

    def reset_selected_area(self):
        """Reset the selected area to select the entire image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.reset_selected_area()

    def set_scanning_size(self, x):
        """
        Set the size of the to be scanned area (aka field of view or the size, which can be scanned with the current
        settings).

        Parameters
        ----------
        x: (float)
            size for X in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scanning_size(x)

    def get_scanning_size(self):
        """
        Returns: (tuple of floats) x and y scanning size in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_size()

    def scanning_size_info(self):
        """Returns: (dict) the scanning size unit and range."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scanning_size_info()

    def set_ebeam_spotsize(self, spotsize):
        """
        Setting the spot size of the ebeam.
        Parameters
        ----------
        spotsize: float
            desired spotsize, unitless
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ebeam_spotsize(spotsize)

    def get_ebeam_spotsize(self):
        """Returns: (float) the current spotsize of the electron beam (unitless)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ebeam_spotsize()

    def spotsize_info(self):
        """Returns: (dict) the unit and range of the spotsize. Unit is None means the spotsize is unitless."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.spotsize_info()

    def set_dwell_time(self, dwell_time):
        """

        Parameters
        ----------
        dwell_time: float
            dwell time in seconds
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_dwell_time(dwell_time)

    def get_dwell_time(self):
        """Returns: (float) the dwell time in seconds."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dwell_time()

    def dwell_time_info(self):
        """Returns: (dict) range of the dwell time and corresponding unit."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.dwell_time_info()

    def set_ht_voltage(self, voltage):
        """
        Set the high voltage.

        Parameters
        ----------
        voltage: float
            Desired high voltage value in volt.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ht_voltage(voltage)

    def get_ht_voltage(self):
        """Returns: (float) the HT Voltage in volt."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ht_voltage()

    def ht_voltage_info(self):
        """Returns: (dict) the unit and range of the HT Voltage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.ht_voltage_info()

    def blank_beam(self):
        """Blank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.blank_beam()

    def unblank_beam(self):
        """Unblank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.unblank_beam()

    def beam_is_blanked(self):
        """Returns: (bool) True if the beam is blanked and False if the beam is not blanked."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_blanked()

    def pump(self):
        """Pump the microscope's chamber. Note that pumping takes some time. This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.pump()

    def get_vacuum_state(self):
        """Returns: (string) the vacuum state of the microscope chamber to see if it is pumped or vented."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_vacuum_state()

    def vent(self):
        """Vent the microscope's chamber. Note that venting takes time (appr. 3 minutes). This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.vent()

    def get_pressure(self):
        """Returns: (float) the chamber pressure in pascal."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_pressure()

    def home_stage(self):
        """Home stage asynchronously. This is non-blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.home_stage()

    def is_homed(self):
        """Returns: (bool) True if the stage is homed and False otherwise."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_homed()

    def set_channel_state(self, name, state):
        """
        Stop or start running the channel. This is non-blocking.

        Parameters
        ----------
        name: str
            name of channel.
        state: bool
            Desired state of the channel, if True set state to run, if False set state to stop.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_channel_state(name, state)

    def wait_for_state_changed(self, desired_state, name, timeout=10):
        """
        Wait until the state of the channel has changed to the desired state, if it has not changed after a certain
        timeout an error will be raised.

        Parameters
        ----------
        desired_state: XT_RUN or XT_STOP
            The state the channel should change into.
        name: str
            name of channel.
        timeout: int
            Amount of time in seconds to wait until the channel state has changed.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.wait_for_state_changed(desired_state, name, timeout)

    def get_channel_state(self, name):
        """Returns: (str) the state of the channel: XT_RUN or XT_STOP."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_channel_state(name)

    def get_free_working_distance(self):
        """Returns: (float) the free working distance in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_free_working_distance()

    def set_free_working_distance(self, free_working_distance):
        """
        Set the free working distance.
        Parameters
        ----------
        free_working_distance: float
            free working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_free_working_distance(free_working_distance)

    def fwd_info(self):
        """Returns the unit and range of the free working distance."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.fwd_info()

    def get_fwd_follows_z(self):
        """
        Returns: (bool) True if Z follows free working distance.
        When Z follows FWD and Z-axis of stage moves, FWD is updated to keep image in focus.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_fwd_follows_z()

    def set_fwd_follows_z(self, follow_z):
        """
        Set if z should follow the free working distance. When Z follows FWD and Z-axis of stage moves, FWD is updated
        to keep image in focus.
        Parameters
        ---------
        follow_z: bool
            True if Z should follow free working distance.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_fwd_follows_z(follow_z)

    def set_autofocusing(self, name, state):
        """
        Set the state of autofocus, beam must be turned on. This is non-blocking.

        Parameters
        ----------
        name: str
            Name of one of the electron channels, the channel must be running.
        state: XT_RUN or XT_STOP
            If state is start, autofocus starts. States cancel and stop both stop the autofocusing. Some microscopes
            might need stop, while others need cancel. The Apreo system requires stop.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_autofocusing(name, state)

    def is_autofocusing(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns: (bool) True if autofocus is running and False if autofocus is not running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_autofocusing(channel_name)

    def set_auto_contrast_brightness(self, name, state):
        """
        Set the state of auto contrast brightness. This is non-blocking.

        Parameters
        ----------
        name: str
            Name of one of the electron channels.
        state: XT_RUN or XT_STOP
            If state is start, auto contrast brightness starts. States cancel and stop both stop the auto contrast
            brightness.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_auto_contrast_brightness(name, state)

    def is_running_auto_contrast_brightness(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns: (bool) True if auto contrast brightness is running and False if auto contrast brightness is not
        running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_running_auto_contrast_brightness(channel_name)

    def get_beam_shift(self):
        """Returns: (float) the current beam shift x and y values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_beam_shift())

    def set_beam_shift(self, x_shift, y_shift):
        """Set the current beam shift values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_shift(x_shift, y_shift)

    def beam_shift_info(self):
        """Returns: (dict) the unit and xy-range of the beam shift."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_shift_info()

    def get_stigmator(self):
        """Returns: (float) the current stigmator x and y values."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stigmator()

    def set_stigmator(self, x, y):
        """Set the current stigmator values."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_stigmator(x, y)

    def stigmator_info(self):
        """Returns: (dict) the unit and xy-range of the stigmator."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stigmator_info()

    def get_rotation(self):
        """Returns: (float) the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_rotation()

    def set_rotation(self, rotation):
        """Set the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_rotation(rotation)

    def rotation_info(self):
        """Returns: (dict) the unit and range of the rotation."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.rotation_info()

    def set_beam_power(self, state):
        """
        Turn on or off the beam power.

        Parameters
        ----------
        state: bool
            True to turn on the beam and False to turn off the beam.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_power(state)

    def get_beam_is_on(self):
        """Returns True if the beam is on and False if the beam is off."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_beam_is_on()

    def is_autostigmating(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns True if autostigmator is running and False if autostigmator is not running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_autostigmating(channel_name)

    def set_autostigmator(self, channel_name, state):
        """
        Set the state of autostigmator, beam must be turned on. This is non-blocking.

        Parameters
        ----------
        channel_name: str
            Name of one of the electron channels, the channel must be running.
        state: XT_RUN or XT_STOP
            State is start, starts the autostigmator. States cancel and stop both stop the autostigmator, some
            microscopes might need stop, while others need cancel.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_autostigmator(channel_name, state)

    def get_pitch(self):
        """
        Get the pitch between two neighboring beams within the multiprobe pattern.

        Returns
        -------
        pitch: float, [um]
            The distance between two beams of the multiprobe pattern.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_pitch()

    def set_pitch(self, pitch):
        """
        Set the pitch between two beams within the multiprobe pattern.

        Returns
        -------
        pitch: float, [um]
            The distance between two beams of the multiprobe pattern.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_pitch(pitch)

    def pitch_info(self):
        """"Returns a dict with the 'unit' and 'range' of the pitch."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pitch_info()

    def get_primary_stigmator(self):
        """
        Get the control values of the primary stigmator. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Each stigmator has two control values; x and y.

        Returns
        -------
        tuple, (x, y) control values of primary stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_primary_stigmator()

    def set_primary_stigmator(self, x, y):
        """
        Set the control values of the primary stigmator. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Each stigmator has two control values; x and y.

        Parameters
        -------
        (x, y) control values of primary stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_primary_stigmator(x, y)

    def primary_stigmator_info(self):
        """"
        Get info about the 'unit' and 'range' of the primary stigmator.

        Returns
        -------
        dict, with keys 'unit' and 'range'
        The key 'unit' gives the physical unit of the stigmator. The key
        'range' returns a dict with the 'x' and 'y' range of the stigmator.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.primary_stigmator_info()

    def get_secondary_stigmator(self):
        """
        Get the control values of the secondary stigmator. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Each stigmator has two control values; x and y.

        Returns
        -------
        tuple, (x, y) control values of primary stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_secondary_stigmator()

    def set_secondary_stigmator(self, x, y):
        """
        Get the control values of the secondary stigmator. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Each stigmator has two control values; x and y.

        Parameters
        -------
        (x, y) control values of primary stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_secondary_stigmator(x, y)

    def secondary_stigmator_info(self):
        """"
        Get info about the 'unit' and 'range' of the secondary stigmator.

        Returns
        -------
        dict, with keys 'unit' and 'range'
        The key 'unit' gives the physical unit of the stigmator. The key
        'range' returns a dict with the 'x' and 'y' range of the stigmator.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.secondary_stigmator_info()

    def get_dc_coils(self):
        """
        Get the four values of the dc coils.

        Returns
        -------
        list of tuples of two floats, len 4
            A list of 4 tuples containing 2 values (floats) of each of the 4 dc coils, in the order:
            [x lower, x upper, y lower, y upper].
            These 4 items describe 4x2 transformation matrix for a required beam shift using DC coils.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dc_coils()

    def get_use_case(self):
        """
        Get the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        Returns
        -------
        state: str, 'MultiBeamTile' or 'SingleBeamlet'

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_use_case()

    def set_use_case(self, state):
        """
        Set the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        Parameters
        ----------
        state: str, 'MultiBeamTile' or 'SingleBeamlet'

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_use_case(state)


class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. Whenever one of these attributes is changed, its
    setter also updates another value if needed.
    """

    def __init__(self, name, role, parent, hfw_nomag, **kwargs):
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        # will take care of executing auto contrast/brightness and auto stigmator asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._hfw_nomag = hfw_nomag

        dwell_time_info = self.parent.dwell_time_info()
        self.dwellTime = model.FloatContinuous(
            self.parent.get_dwell_time(),
            dwell_time_info["range"],
            unit=dwell_time_info["unit"],
            setter=self._setDwellTime)

        voltage_info = self.parent.ht_voltage_info()
        init_voltage = numpy.clip(self.parent.get_ht_voltage(), voltage_info['range'][0], voltage_info['range'][1])
        self.accelVoltage = model.FloatContinuous(
            init_voltage,
            voltage_info["range"],
            unit=voltage_info["unit"],
            setter=self._setVoltage
        )

        self.blanker = model.BooleanVA(
            self.parent.beam_is_blanked(),
            setter=self._setBlanker)

        spotsize_info = self.parent.spotsize_info()
        self.spotSize = model.FloatContinuous(
            self.parent.get_ebeam_spotsize(),
            spotsize_info["range"],
            unit=spotsize_info["unit"],
            setter=self._setSpotSize)

        beam_shift_info = self.parent.beam_shift_info()
        range_x = beam_shift_info["range"]["x"]
        range_y = beam_shift_info["range"]["y"]
        self.beamShift = model.TupleContinuous(
            self.parent.get_beam_shift(),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(int, float),
            unit=beam_shift_info["unit"],
            setter=self._setBeamShift)

        rotation_info = self.parent.rotation_info()
        self.rotation = model.FloatContinuous(
            self.parent.get_rotation(),
            rotation_info["range"],
            unit=rotation_info["unit"],
            setter=self._setRotation)

        scanning_size_info = self.parent.scanning_size_info()
        fov = self.parent.get_scanning_size()[0]
        self.horizontalFoV = model.FloatContinuous(
            fov,
            unit=scanning_size_info["unit"],
            range=scanning_size_info["range"]["x"],
            setter=self._setHorizontalFoV)

        mag = self._hfw_nomag / fov
        mag_range_max = self._hfw_nomag / scanning_size_info["range"]["x"][0]
        mag_range_min = self._hfw_nomag / scanning_size_info["range"]["x"][1]
        self.magnification = model.FloatContinuous(mag, unit="",
                                                   range=(mag_range_min, mag_range_max),
                                                   readonly=True)
        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e3),
                                                  unit="m", readonly=True)
        self._updateDepthOfField()

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling")
        self._va_poll.start()

    # TODO Commented out code because it is currently not supproted by XT. An update or another implementation may be
    # made later

    # @isasync
    # def applyAutoStigmator(self, detector):
    #     """
    #     Wrapper for running the auto stigmator functionality asynchronously. It sets the state of autostigmator,
    #     the beam must be turned on and unblanked. This call is non-blocking.
    #
    #     :param detector (str): Role of the detector.
    #     :return: Future object
    #     """
    #     # Create ProgressiveFuture and update its state
    #     est_start = time.time() + 0.1
    #     f = ProgressiveFuture(start=est_start,
    #                           end=est_start + 8)  # rough time estimation
    #     f._auto_stigmator_lock = threading.Lock()
    #     f._must_stop = threading.Event()  # cancel of the current future requested
    #     f.task_canceller = self._cancelAutoStigmator
    #     if DETECTOR2CHANNELNAME[detector] != "electron1":
    #         # Auto stigmation is only supported on channel electron1, not on the other channels
    #         raise KeyError("This detector is not supported for auto stigmation")
    #     f.c = DETECTOR2CHANNELNAME[detector]
    #     return self._executor.submitf(f, self._applyAutoStigmator, f)
    #
    # def _applyAutoStigmator(self, future):
    #     """
    #     Starts applying auto stigmator and checks if the process is finished for the ProgressiveFuture object.
    #     :param future (Future): the future to start running.
    #     """
    #     channel_name = future._channel_name
    #     with future._auto_stigmator_lock:
    #         if future._must_stop.is_set():
    #             raise CancelledError()
    #         self.parent.set_autostigmator(channel_name, XT_RUN)
    #         time.sleep(0.5)  # Wait for the auto stigmator to start
    #
    #     # Wait until the microscope is no longer applying auto stigmator
    #     while self.parent.is_autostigmating(channel_name):
    #         future._must_stop.wait(0.1)
    #         if future._must_stop.is_set():
    #             raise CancelledError()
    #
    # def _cancelAutoStigmator(self, future):
    #     """
    #     Cancels the auto stigmator. Non-blocking.
    #     :param future (Future): the future to stop.
    #     :return (bool): True if it successfully cancelled (stopped) the move.
    #     """
    #     future._must_stop.set()  # tell the thread taking care of auto stigmator it's over
    #
    #     with future._auto_stigmator_lock:
    #         logging.debug("Cancelling auto stigmator")
    #         self.parent.set_autostigmator(future._channel_name, XT_STOP)
    #         return True

    @isasync
    def applyAutoContrastBrightness(self, detector):
        """
        Wrapper for running the automatic setting of the contrast brightness functionality asynchronously. It
        automatically sets the contrast and the brightness via XT, the beam must be turned on and unblanked. Auto
        contrast brightness functionality works best if there is a feature visible in the image. This call is
        non-blocking.

        :param detector (str): Role of the detector.
        :return: Future object

        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 20)  # Rough time estimation
        f._auto_contrast_brighness_lock = threading.Lock()
        f._must_stop = threading.Event()  # Cancel of the current future requested
        f.task_canceller = self._cancelAutoContrastBrightness
        f._channel_name = DETECTOR2CHANNELNAME[detector]
        return self._executor.submitf(f, self._applyAutoContrastBrightness, f)

    def _applyAutoContrastBrightness(self, future):
        """
        Starts applying auto contrast brightness and checks if the process is finished for the ProgressiveFuture object.
        :param future (Future): the future to start running.
        """
        channel_name = future._channel_name
        with future._auto_contrast_brighness_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            self.parent.set_auto_contrast_brightness(channel_name, XT_RUN)
            time.sleep(0.5)  # Wait for the auto contrast brightness to start

        # Wait until the microscope is no longer performing auto contrast brightness
        while self.parent.is_running_auto_contrast_brightness(channel_name):
            future._must_stop.wait(0.1)
            if future._must_stop.is_set():
                raise CancelledError()

    def _cancelAutoContrastBrightness(self, future):
        """
        Cancels the auto contrast brightness. Non-blocking.
        :param future (Future): the future to stop.
        :return (bool): True if it successfully cancelled (stopped) the move.
        """
        future._must_stop.set()  # Tell the thread taking care of auto contrast brightness it's over.

        with future._auto_contrast_brighness_lock:
            logging.debug("Cancelling auto contrast brightness")
            try:
                self.parent.set_auto_contrast_brightness(future._channel_name, XT_STOP)
                return True
            except OSError as error_msg:
                logging.warning("Failed to cancel auto brightness contrast: %s", error_msg)
                return False

    def _updateSettings(self):
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """
        logging.debug("Updating SEM settings")
        try:
            dwell_time = self.parent.get_dwell_time()
            if dwell_time != self.dwellTime.value:
                self.dwellTime._value = dwell_time
                self.dwellTime.notify(dwell_time)
            voltage = self.parent.get_ht_voltage()
            v_range = self.accelVoltage.range
            if not v_range[0] <= voltage <= v_range[1]:
                logging.info("Voltage {} V is outside of range {}, clipping to nearest value.".format(voltage, v_range))
                voltage = self.accelVoltage.clip(voltage)
            if voltage != self.accelVoltage.value:
                self.accelVoltage._value = voltage
                self.accelVoltage.notify(voltage)
            blanked = self.parent.beam_is_blanked()
            if blanked != self.blanker.value:
                self.blanker._value = blanked
                self.blanker.notify(blanked)
            spot_size = self.parent.get_ebeam_spotsize()
            if spot_size != self.spotSize.value:
                self.spotSize._value = spot_size
                self.spotSize.notify(spot_size)
            beam_shift = self.parent.get_beam_shift()
            if beam_shift != self.beamShift.value:
                self.beamShift._value = beam_shift
                self.beamShift.notify(beam_shift)
            rotation = self.parent.get_rotation()
            if rotation != self.rotation.value:
                self.rotation._value = rotation
                self.rotation.notify(rotation)
            fov = self.parent.get_scanning_size()[0]
            if fov != self.horizontalFoV.value:
                self.horizontalFoV._value = fov
                mag = self._hfw_nomag / fov
                self.magnification._value = mag
                self.horizontalFoV.notify(fov)
                self.magnification.notify(mag)
        except Exception:
            logging.exception("Unexpected failure when polling settings")

    def _setDwellTime(self, dwell_time):
        self.parent.set_dwell_time(dwell_time)
        return self.parent.get_dwell_time()

    def _setVoltage(self, voltage):
        self.parent.set_ht_voltage(voltage)
        return self.parent.get_ht_voltage()

    def _setBlanker(self, blank):
        """True if the the electron beam should blank, False if it should be unblanked."""
        if blank:
            self.parent.blank_beam()
        else:
            self.parent.unblank_beam()
        return self.parent.beam_is_blanked()

    def _setSpotSize(self, spotsize):
        self.parent.set_ebeam_spotsize(spotsize)
        return self.parent.get_ebeam_spotsize()

    def _setBeamShift(self, beam_shift):
        self.parent.set_beam_shift(*beam_shift)
        return self.parent.get_beam_shift()

    def _setRotation(self, rotation):
        self.parent.set_rotation(rotation)
        return self.parent.get_rotation()

    def _setHorizontalFoV(self, fov):
        self.parent.set_scanning_size(fov)
        fov = self.parent.get_scanning_size()[0]
        mag = self._hfw_nomag / fov
        self.magnification._value = mag
        self.magnification.notify(mag)
        self._updateDepthOfField()
        return fov

    def _updateDepthOfField(self):
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the TFS stage and updating the position.
    """

    def __init__(self, name, role, parent, rng=None, **kwargs):
        if rng is None:
            rng = {}
        stage_info = parent.stage_info()
        if "x" not in rng:
            rng["x"] = stage_info["range"]["x"]
        if "y" not in rng:
            rng["y"] = stage_info["range"]["y"]
        if "z" not in rng:
            rng["z"] = stage_info["range"]["z"]

        axes_def = {
            # Ranges are from the documentation
            "x": model.Axis(unit="m", range=rng["x"]),
            "y": model.Axis(unit="m", range=rng["y"]),
            "z": model.Axis(unit="m", range=rng["z"]),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def,
                                **kwargs)
        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self.position = model.VigilantAttribute({}, unit=stage_info["unit"],
                                                readonly=True)
        self._updatePosition()

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Position polling")
        self._pos_poll.start()

    def _updatePosition(self, raw_pos=None):
        """
        update the position VA
        raw_pos (dict str -> float): the position in mm (as received from the SEM)
        """
        if raw_pos is None:
            position = self.parent.get_stage_position()
            x, y, z = position["x"], position["y"], position["z"]
        else:
            x, y, z = raw_pos["x"], raw_pos["y"], raw_pos["z"]

        pos = {"x": x,
               "y": y,
               "z": z,
               }
        self.position._set_value(self._applyInversion(pos), force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM stage position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _moveTo(self, future, pos, timeout=60):
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                logging.debug("Moving to position {}".format(pos))
                self.parent.move_stage(pos, rel=False)
                time.sleep(0.5)

                # Wait until the move is over.
                # Don't check for future._must_stop because anyway the stage will
                # stop moving, and so it's nice to wait until we know the stage is
                # not moving.
                moving = True
                tstart = time.time()
                while moving:
                    pos = self.parent.get_stage_position()
                    moving = self.parent.stage_is_moving()
                    # Take the opportunity to update .position
                    self._updatePosition(pos)

                    if time.time() > tstart + timeout:
                        self.parent.stop_stage_movement()
                        logging.error("Timeout after submitting stage move. Aborting move.")
                        break

                    # Wait for 50ms so that we do not keep using the CPU all the time.
                    time.sleep(50e-3)

                # If it was cancelled, Abort() has stopped the stage before, and
                # we still have waited until the stage stopped moving. Now let
                # know the user that the move is not complete.
                if future._must_stop.is_set():
                    raise CancelledError()
            except Exception:
                if future._must_stop.is_set():
                    raise CancelledError()
                raise
            finally:
                future._was_stopped = True
                # Update the position, even if the move didn't entirely succeed
                self._updatePosition()

    def _doMoveRel(self, future, shift):
        pos = self.parent.get_stage_position()
        for k, v in shift.items():
            pos[k] += v

        target_pos = self._applyInversion(pos)
        # Check range (for the axes we are moving)
        for an in shift.keys():
            rng = self.axes[an].range
            p = target_pos[an]
            if not rng[0] <= p <= rng[1]:
                raise ValueError("Relative move would cause axis %s out of bound (%g m)" % (an, p))

        self._moveTo(future, pos)

    @isasync
    def moveRel(self, shift):
        """
        Shift the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        shift: dict(string->float)
            Relative shift to move the stage to per axes in m. Axes are 'x' and 'y'.
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveAbs(self, future, pos):
        self._moveTo(future, pos)

    @isasync
    def moveAbs(self, pos):
        """
        Move the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        pos: dict(string->float)
            Absolute position to move the stage to per axes in m. Axes are 'x' and 'y'.
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def stop(self, axes=None):
        """Stop the movement of the stage."""
        self._executor.cancel()
        self.parent.stop_stage_movement()
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _createFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative). Non-blocking.
        future (Future): the future to stop. Unused, only one future must be
         running at a time.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (not finished requesting axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current move")
        future._must_stop.set()  # tell the thread taking care of the move it's over
        self.parent.stop_stage_movement()

        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped


class Focus(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the SEM focus (as it's considered an axis in Odemis)
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        axes (set of string): names of the axes
        """

        fwd_info = parent.fwd_info()
        axes_def = {
            "z": model.Axis(unit=fwd_info["unit"], range=fwd_info["range"]),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the server must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Position polling")
        self._pos_poll.start()

    @isasync
    def applyAutofocus(self, detector):
        """
        Wrapper for running the autofocus functionality asynchronously. It sets the state of autofocus,
        the beam must be turned on and unblanked. Also a a reasonable manual focus is needed. When the image is too far
        out of focus, an incorrect focus can be found using the autofocus functionality.
        This call is non-blocking.

        :param detector (str): Role of the detector.
        :param state (str):  "run", or "stop"
        :return: Future object
        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 11)  # rough time estimation
        f._autofocus_lock = threading.Lock()
        f._must_stop = threading.Event()  # cancel of the current future requested
        f.task_canceller = self._cancelAutoFocus
        f._channel_name = DETECTOR2CHANNELNAME[detector]
        return self._executor.submitf(f, self._applyAutofocus, f)

    def _applyAutofocus(self, future):
        """
        Starts autofocussing and checks if the autofocussing process is finished for ProgressiveFuture.
        :param future (Future): the future to start running.
        """
        channel_name = future._channel_name
        with future._autofocus_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            self.parent.set_autofocusing(channel_name, XT_RUN)
            time.sleep(0.5)  # Wait for the autofocussing to start

        # Wait until the microscope is no longer autofocussing
        while self.parent.is_autofocusing(channel_name):
            future._must_stop.wait(0.1)
            if future._must_stop.is_set():
                raise CancelledError()

    def _cancelAutoFocus(self, future):
        """
        Cancels the autofocussing. Non-blocking.
        :param future (Future): the future to stop.
        :return (bool): True if it successfully cancelled (stopped) the move.
        """
        future._must_stop.set()  # tell the thread taking care of autofocussing it's over

        with future._autofocus_lock:
            logging.debug("Cancelling autofocussing")
            try:
                self.parent.set_autofocusing(future._channel_name, XT_STOP)
                return True
            except OSError as error_msg:
                logging.warning("Failed to cancel autofocus: %s", error_msg)
                return False

    def _updatePosition(self):
        """
        update the position VA
        """
        z = self.parent.get_free_working_distance()
        self.position._set_value({"z": z}, force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM stage position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _doMoveRel(self, foc):
        """
        move by foc
        foc (float): relative change in m
        """
        try:
            foc += self.parent.get_free_working_distance()
            self.parent.set_free_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    def _doMoveAbs(self, foc):
        """
        move to pos
        foc (float): unit m
        """
        try:
            self.parent.set_free_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    @isasync
    def moveRel(self, shift):
        """
        shift (dict): shift in m
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        foc = shift["z"]
        f = self._executor.submit(self._doMoveRel, foc)
        return f

    @isasync
    def moveAbs(self, pos):
        """
        pos (dict): pos in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        foc = pos["z"]
        f = self._executor.submit(self._doMoveAbs, foc)
        return f

    def stop(self, axes=None):
        """
        Stop the last command
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()
        logging.debug("Cancelled all ebeam focus moves")

        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")
