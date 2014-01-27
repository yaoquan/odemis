# -*- coding: utf-8 -*-

""" This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.


@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Purpose:

This module contains classes that allow to create ToolBars for the MicroscopeGUI.

"""

from odemis.gui import model, img
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton
import wx

# List of tools available
TOOL_RO_ZOOM = 1 # Select the region to zoom in
TOOL_ROI = 2 # Select the region of interest (sub-area to be updated)
TOOL_ROA = 3 # Select the region of acquisition (area to be acquired, SPARC-only)
TOOL_ZOOM_FIT = 4 # Select a zoom to fit the current image content
TOOL_POINT = 5 # Select a point
TOOL_LINE = 6 # Select a line
TOOL_DICHO = 7 # Dichotomy mode to select a sub-quadrant
TOOL_SPOT = 8 # Select spot mode on the SEM
TOOL_DRIFTCOR = 9

# Two types of tools:
# * mode: they are toggle buttons, changing the tool mode of the GUIModel
# * action: they are just click button, and call a function when pressed

class Tool(object):
    def __init__(self, icon, tooltip=None):
        """
        icon (string): name of the bitmap without .png, _h.png, _a.png
         (iow, as found in gui.img.data)
        tooltip (string): tool tip content
        """
        self.icon = icon
        self.tooltip = tooltip

class ModeTool(Tool):
    def __init__(self, icon, value_on, value_off, tooltip=None):
        """
        value_on (anything): value to set to the VA when the tool is activated
        value_on (anything): value to set when the tool is explicitly disabled
        """
        Tool.__init__(self, icon, tooltip=tooltip)
        self.value_on = value_on
        self.value_off = value_off

class ActionTool(Tool):
    pass

TOOLS = {TOOL_RO_ZOOM:
            ModeTool(
                "btn_view_zoom",
                model.TOOL_ZOOM,
                model.TOOL_NONE,
                "Select region of zoom"
            ),
         TOOL_ROI:
            ModeTool(
                "btn_view_update",
                model.TOOL_ROI,
                model.TOOL_NONE,
                "Select region of interest"
            ),
         TOOL_ROA:
            ModeTool(
                "btn_view_sel",
                model.TOOL_ROA,
                model.TOOL_NONE,
                "Select region of acquisition"
            ),
         TOOL_POINT:
            ModeTool(
                "btn_view_pick",
                model.TOOL_POINT,
                model.TOOL_NONE,
                "Select point"
            ),
         TOOL_LINE:
            ModeTool(
                "btn_view_pick", # TODO icon
                model.TOOL_LINE,
                model.TOOL_NONE,
                "Select line"
            ),
         TOOL_DICHO:
            ModeTool(
                "btn_view_dicho",
                model.TOOL_DICHO,
                model.TOOL_NONE,
                "Dichotomic search for e-beam centre"
            ),
         TOOL_SPOT:
            ModeTool(
                "btn_view_spot",
                model.TOOL_SPOT,
                model.TOOL_NONE,
                "E-beam spot mode"
            ),
         TOOL_ZOOM_FIT:
            ActionTool(
                "btn_view_resize",
                "Zoom to fit content"
            ),
         TOOL_DRIFTCOR:
            ModeTool(
                "btn_view_sel",
                model.TOOL_DRIFTCOR,
                model.TOOL_NONE,
                "Select region for drift correction"
            ),
        }


class ToolBar(wx.Panel):

    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)
        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

        # Create orientation dependent objects
        if kwargs['style'] & wx.VERTICAL:
            self.orientation = wx.VERTICAL
            main_sizer = wx.BoxSizer(wx.VERTICAL)
            first_bmp = wx.StaticBitmap(self, -1,
                                        img.data.getside_menu_topBitmap())
            second_bmp = wx.StaticBitmap(self, -1,
                                         img.data.getside_menu_bottomBitmap())
            self.btn_sizer = wx.BoxSizer(wx.VERTICAL)
        else:
            self.orientation = wx.HORIZONTAL
            main_sizer = wx.BoxSizer(wx.HORIZONTAL)
            first_bmp = wx.StaticBitmap(self, -1,
                                        img.data.getside_menu_leftBitmap())
            second_bmp = wx.StaticBitmap(self, -1,
                                         img.data.getside_menu_rightBitmap())
            self.btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Set the main sizer that will contain the elements that will form
        # the toolbar bar.
        self.SetSizer(main_sizer)

        # Add the left or top image
        main_sizer.Add(first_bmp)

        # Create a panel that will hold the actual buttons
        self.btn_panel = wx.Panel(self, -1)
        self.btn_panel.SetBackgroundColour(wx.BLACK)
        self.btn_panel.SetSizer(self.btn_sizer)

        # Add the button panel to the toolbar
        main_sizer.Add(self.btn_panel)

        main_sizer.Add(second_bmp)

        if self.orientation == wx.VERTICAL:
            main_sizer.SetItemMinSize(self.btn_panel, 40, -1)
        else:
            main_sizer.SetItemMinSize(self.btn_panel, -1, 36)

        self._buttons = {}
        # References of va callbacks are stored in this list, to prevent
        # unsubscription
        self._mode_callbacks = []


    def add_tool(self, tool_id, handler):
        """ Add a tool and it's event handler to the toolbar

        tool_id (TOOL_*): button to be displayed
        handler (VA or callable): if mode: VA, if action: callable
        value (object): value for the VA
        raises:
            KeyError: if tool_id is incorrect
        """
        tooltype = TOOLS[tool_id]
        if isinstance(tooltype, ActionTool):
            self._add_action_tool(tooltype, tool_id, handler)
        elif isinstance(tooltype, ModeTool):
            self._add_mode_tool(tooltype, tool_id, handler)

    def _add_action_tool(self, tooltype, tool_id, callback):
        btn = self._add_button(ImageButton, tooltype.icon, tooltype.tooltip)
        btn.Bind(wx.EVT_BUTTON, callback)
        self._buttons[tool_id] = btn

    def _add_mode_tool(self, tooltype, tool_id, va):
        btn = self._add_button(
                        ImageToggleButton,
                        tooltype.icon,
                        tooltype.tooltip
            )
        self._buttons[tool_id] = btn

        value_on = tooltype.value_on
        value_off = tooltype.value_off

        # functions to handle synchronization VA <-> toggle button
        def _on_click(evt, va=va, value_on=value_on, value_off=value_off):
            if evt.isDown:
                va.value = value_on
            else:
                va.value = value_off

        def _on_va_change(new_value, value_on=value_on, btn=btn):
            btn.SetToggle(new_value == value_on)

        # FIXME: It doesn't generate evt_togglebutton
        btn.Bind(wx.EVT_BUTTON, _on_click)
        va.subscribe(_on_va_change)
        self._mode_callbacks.append(_on_va_change)

    def _add_button(self, cls, img_prefix, tooltip=None):
        bmp = img.data.catalog[img_prefix].GetBitmap()
        bmpa = img.data.catalog[img_prefix + "_a"].GetBitmap()
        bmph = img.data.catalog[img_prefix + "_h"].GetBitmap()
        bmpd = img.data.catalog[img_prefix + "_h"].GetBitmap()

        btn = cls(self.btn_panel, bitmap=bmp, size=(24, 24))
        btn.SetBitmapSelected(bmpa)
        btn.SetBitmapHover(bmph)
        btn.SetBitmapDisabled(bmpd)

        if tooltip:
            btn.SetToolTipString(tooltip)

        if self.orientation == wx.HORIZONTAL:
            f = wx.LEFT | wx.RIGHT | wx.TOP
            b = 5
        else:
            f = wx.BOTTOM | wx.LEFT
            b = 10

        self.btn_sizer.Add(btn, border=b, flag=f)
        self.btn_panel.Layout()
        return btn

    def enable_button(self, tool_id, enable):
        self._buttons[tool_id].Enable(enable)

    def enable(self, enabled):
        """ TODO: make a cleverer version that stores the curent state when
        a first disable is called?"""
        for _, btn in self._buttons.items():
            btn.Enable(enabled)
