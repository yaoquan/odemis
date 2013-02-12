#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Created on 2 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import ctypes
import logging
import math
import os
import threading
import time
import wx


# A class for smooth, flicker-less display of anything on a window, with drag
# and zoom capability a bit like:
# wx.canvas, wx.BufferedWindow, BufferedCanvas, wx.floatcanvas, wx.scrolledwindow...
# The main differences are:
#  * when dragging the window the surrounding margin is already computed
#  * You can draw at any coordinate, and it's displayed if the user has dragged the canvas close from the area.
#  * Built-in optimised zoom/transparency for 2 images
# Maybe could be replaced by a GLCanvas + magic, or a Cairo Canvas

class DraggableCanvas(wx.Panel):
    """
    A draggable, buffered window class.

    To use it, instantiate it and then put what you want to display in the lists:
    * Images: for the two images to display
    * WorldOverlays: for additional objects to display (should have a Draw(dc) method)
    * ViewOverlays: for additional objects that stay at an absolute position

    The idea = three layers of decreasing area size:
    * The whole world, which can have infinite dimensions, but needs a redraw
    * The buffer, which contains a precomputed image of the world big enough that a drag cannot bring it outside of the viewport
    * The viewport, which is what the user sees

    Unit: at scale = 1, 1px = 1 unit. So an image with scale = 1 will be
      displayed actual size.

    """
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, style=wx.NO_FULL_REPAINT_ON_RESIZE)
        # TODO: would be better to have one list, with 2  types of objects (view, world)
        self.WorldOverlays = [] # on top of the pictures, relative position
        self.ViewOverlays = [] # on top, stays at an absolute position
        self.Images = [None] # should always have at least 1 element, to allow adding directly a 2nd image
        self.merge_ratio = 0.3
        # self.zoom = 0 # float, can also be negative
        self.scale = 1.0 # derived from zoom
        # self.zoom_range = (-10.0, 10.0)

        self.world_pos_buffer = (0, 0) # centre pos of the buffer in the world
        # the position the view is asking to the next buffer recomputation
        self.world_pos_requested = self.world_pos_buffer # in buffer-coordinates: =1px at scale = 1

        # buffer = the whole image to be displayed
        self._dcBuffer = wx.MemoryDC()

        self.buffer_size = (1, 1) # very small first, so that for sure it'll be resized with OnSize
        self.ResizeBuffer(self.buffer_size)
        # When resizing, margin to put around the current size
        self.margin = 512

        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on Linux...
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)
        self.SetBackgroundColour('black')

        # DEBUG
        # self.SetBackgroundColour('grey') # (grey is for debugging)
        # self.margin = 2

        # view = the area displayed
        self.drag_shift = (0, 0) # px, px: Current shift to world_pos_buffer in the actual view
        self.dragging = False
        self.drag_init_pos = (0, 0) # px, px: initial position of mouse when started dragging

        self._rdragging = False
        self._rdrag_init_pos = None # (int, int) px
        self._rdrag_prev_value = None # (float, float) last absolute value, for sending the change

        # timer to give a delay before redrawing so we wait to see if there are
        # several events waiting
        self.DrawTimer = wx.PyTimer(self.OnDrawTimer)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_MOTION, self.OnMouseMotion)
        self.Bind(wx.EVT_LEFT_DCLICK, self.OnDblClick)
        self.Bind(wx.EVT_RIGHT_DOWN, self.OnRightDown)
        self.Bind(wx.EVT_RIGHT_UP, self.OnRightUp)

        self.Bind(wx.EVT_CHAR, self.OnChar)

    def OnChar(self, event):
        key = event.GetKeyCode()

        change = 16
        if event.ShiftDown():
            change = 2 # softer

        if key == wx.WXK_LEFT:
            self.ShiftView((change, 0))
        elif key == wx.WXK_RIGHT:
            self.ShiftView((-change, 0))
        elif key == wx.WXK_DOWN:
            self.ShiftView((0, -change))
        elif key == wx.WXK_UP:
            self.ShiftView((0, change))

    def OnRightDown(self, event):
        if self.dragging:
            return

        self._rdragging = True
        self._rdrag_init_pos = event.GetPositionTuple()
        self._rdrag_prev_value = [0, 0]
        self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
        if not self.HasCapture():
            self.CaptureMouse()

        # Get the focus back when receiving a click
        self.SetFocus()

    def OnRightUp(self, event):
        if self._rdragging:
            self._rdragging = False
            self.SetCursor(wx.STANDARD_CURSOR)
            if self.HasCapture():
                self.ReleaseMouse()

    def ShiftView(self, shift):
        """ Moves the position of the view by a delta
        shift (2-tuple int): delta in buffer coordinates (pixels)
        """
        self.ReCenterBuffer((self.world_pos_buffer[0] - (shift[0] / self.scale),
                            self.world_pos_buffer[1] - (shift[1] / self.scale)))

    def OnLeftDown(self, event):
        if self._rdragging:
            return

        self.dragging = True
        # There might be several draggings before the buffer is updated
        # So take into account the current drag_shift to compensate
        pos = event.GetPositionTuple()
        self.drag_init_pos = (pos[0] - self.drag_shift[0],
                              pos[1] - self.drag_shift[1])
        self.SetCursor(wx.StockCursor(wx.CURSOR_SIZING))
        if not self.HasCapture():
            self.CaptureMouse()

        # Get the focus back when receiving a click
        self.SetFocus()

    def OnLeftUp(self, event):
        if self.dragging:
            self.dragging = False
            self.SetCursor(wx.STANDARD_CURSOR)
            if self.HasCapture():
                self.ReleaseMouse()

            # Update the position of the buffer to where the view is centered
            # self.drag_shift is the delta we want to apply
            new_pos = (self.world_pos_buffer[0] - self.drag_shift[0] / self.scale,
                       self.world_pos_buffer[1] - self.drag_shift[1] / self.scale)
            self.ReCenterBuffer(new_pos)

    def OnMouseMotion(self, event):
        if self.dragging:
            pos = event.GetPositionTuple()
            self.drag_shift = (pos[0] - self.drag_init_pos[0],
                               pos[1] - self.drag_init_pos[1])
            self.Refresh()

        if self._rdragging:
            # TODO: make it non-linear:
            # the further from the original point, the more it moves for one pixel
            # => use 3 points: starting point, previous point, current point
            # if dis < 32 px => min : dis (small linear zone)
            # else: dis + 1/32 * sign* (dis-32)**2 => (square zone)
            # send diff between value and previous value sent => it should always be at the same position for the cursor at the same place
            linear_zone = 32.0
            pos = event.GetPositionTuple()
            for i in range(2):
                shift = pos[i] - self._rdrag_init_pos[i]
                if abs(shift) <= linear_zone:
                    value = shift
                else:
                    ssquare = cmp(shift, 0) * (shift - linear_zone)**2
                    value = shift + ssquare / linear_zone
                change = value - self._rdrag_prev_value[i]
                if change:
                    self.onExtraAxisMove(i, change)
                    self._rdrag_prev_value[i] = value

    def OnDblClick(self, event):
        pos = event.GetPositionTuple()
        center = (self.ClientSize[0] / 2, self.ClientSize[1] / 2)
        shift = (center[0] - pos[0],
                 center[1] - pos[1])

        # shift the view instantly
        self.drag_shift = (self.drag_shift[0] + shift[0],
                           self.drag_shift[1] + shift[1])
        self.Refresh()

        # recompute the view
        new_pos = (self.world_pos_buffer[0] - shift[0] / self.scale,
                   self.world_pos_buffer[1] - shift[1] / self.scale)
        logging.debug("double click at %s", new_pos)
        self.ReCenterBuffer(new_pos)

    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)
        axis (0<int): the axis modified
            0 => right vertical
            1 => right horizontal
        shift (int): relative amount of pixel moved
            >0: toward up/right
        """
        # We have nothing to do
        # Inheriting classes can do more
        pass

    # Change picture one/two
    def SetImage(self, index, im, pos = None, scale = None):
        """
        Set (or update) the image
        index (0<=int): index number of the image, can be up to 1 more than the current number of images
        im (wx.Image): the image, or None to remove the current image
        pos (2-tuple of float): position of the center of the image (in world unit)
        scale (float): scaling of the image
        Note: call ShouldUpdateDrawing() to actually get the image redrawn afterwards
        """
        assert(0 <= index <= len(self.Images))

        if im is None: # Delete the image
            # always keep at least a length of 1
            if index == 0:
                # just replace by None
                self.Images[index] = None
            else:
                del self.Images[index]
        else:
            im._dc_center = pos
            im._dc_scale = scale
            if not im.HasAlpha():
                im.InitAlpha()
            if index == len(self.Images):
                # increase the size
                self.Images.append(im)
            else:
                # replace
                self.Images[index] = im

    def OnPaint(self, event):
        """ Quick update of the window content with the buffer + the static
        overlays
        """
        dc = wx.PaintDC(self)
        margin = ((self.buffer_size[0] - self.ClientSize[0])/2,
                  (self.buffer_size[1] - self.ClientSize[1])/2)

        # dc.BlitPointSize(self.drag_shift,
        #                  self.buffer_size,
        #                  self._dcBuffer,
        #                  (0,0))
        dc.BlitPointSize((0, 0), self.ClientSize, self._dcBuffer,
                         (margin[0] - self.drag_shift[0],
                            margin[1] - self.drag_shift[1]))

        # TODO do this only when drag_shift changes, and record the modified region before and put back after.
        self.DrawStaticOverlays(dc)

    def OnSize(self, event):
        """ Ensures that the buffer still fits in the view and recenter the view
        """
        # Make sure the buffer is always at least the same size as the Window or bigger
        new_size = (max(self.buffer_size[0], self.ClientSize[0] + self.margin * 2),
                    max(self.buffer_size[1], self.ClientSize[1] + self.margin * 2))

        # recenter the view
        if (new_size != self.buffer_size):
            self.ResizeBuffer(new_size)
            # self.ReCenterBuffer((new_size[0]/2, new_size[1]/2))
            self.ShouldUpdateDrawing()
        else:
            self.Refresh(eraseBackground=False)

    def ResizeBuffer(self, size):
        """
        Updates the size of the buffer to the given size
        size (2-tuple int)
        """
        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it
        self._buffer = wx.EmptyBitmap(*size)
        self.buffer_size = size
        self._dcBuffer.SelectObject(self._buffer)
        self._dcBuffer.SetBackground(wx.BLACK_BRUSH) # On Linux necessary after every select object

    def ReCenterBuffer(self, pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the world coordinates of the center of the buffer
        Warning: always call from the main GUI thread. So if you're not sure
         in which thread you are, do:
         wx.CallAfter(canvas.ReCenterBuffer, pos)
        """
        if self.world_pos_requested == pos:
            return
        self.world_pos_requested = pos

        # TODO: we need also to save the scale requested
        # FIXME: could maybe be more clever and only request redraw for the
        # outside region
        self.ShouldUpdateDrawing()

    def ShouldUpdateDrawing(self, period=0.1):
        """
        Schedule the update of the buffer
        period (second): maximum time to wait before it will be updated
        Warning: always call from the main GUI thread. So if you're not sure
         in which thread you are, do:
         wx.CallAfter(canvas.ShouldUpdateDrawing)
        """
        if not self.DrawTimer.IsRunning():
            self.DrawTimer.Start(period * 1000.0, oneShot=True)

    def OnDrawTimer(self):
        # logging.debug("Drawing timer in thread %s", threading.current_thread().name)
        self.UpdateDrawing()

    def UpdateDrawing(self):
        """
        Redraws everything (that is viewed in the buffer)
        """
        prev_world_pos = self.world_pos_buffer
        self.Draw(self._dcBuffer)

        shift_view = ((self.world_pos_buffer[0] - prev_world_pos[0]) * self.scale,
                      (self.world_pos_buffer[1] - prev_world_pos[1]) * self.scale)
        # everything is redrawn centred, so reset drag_shift
        if self.dragging:
            self.drag_init_pos = (self.drag_init_pos[0] - shift_view[0],
                                  self.drag_init_pos[1] - shift_view[1])
            self.drag_shift = (self.drag_shift[0] + shift_view[0],
                               self.drag_shift[1] + shift_view[1])
        else:
            # in theory, it's the same, but just to be sure we reset to 0,0 exactly
            self.drag_shift = (0, 0)

        # eraseBackground doesn't seem to matter, but just in case...
        self.Refresh(eraseBackground=False)
        # self.Update() # not really necessary as refresh causes an onPaint event soon, but makes it slightly sooner, so smoother

    def Draw(self, dc):
        """
        Redraw the buffer with the images and overlays
        dc (wx.DC)
        overlays must have a Draw(dc, shift, scale) method
        """
        self.world_pos_buffer = self.world_pos_requested
        #logging.debug("New drawing at %s", self.world_pos_buffer)
        dc.Clear()
        # set and reset the origin here because Blit in onPaint gets "confused" with values > 2048
        # centred on self.world_pos_buffer
        dc.SetDeviceOriginPoint((self.buffer_size[0] / 2, self.buffer_size[1] / 2))
        # we do not use the UserScale of the DC here because it would lead
        # to scaling computation twice when the image has a scale != 1. In
        # addition, as coordinates are int, there is rounding error on zooming.

        self._DrawMergedImages(dc, self.Images, self.merge_ratio)

        # Each overlay draws itself
        for o in self.WorldOverlays:
            o.Draw(dc, self.world_pos_buffer, self.scale)

        dc.SetDeviceOriginPoint((0, 0))

    def DrawStaticOverlays(self, dc):
        """ Draws all the static overlays on the DC dc (wx.DC)
        """
        # center the coordinates
        dc.SetDeviceOrigin(self.ClientSize[0]/2, self.ClientSize[1]/2)
        for o in self.ViewOverlays:
            o.Draw(dc)

    # TODO: see if with Numpy it's faster (~less memory copy),
    # cf http://wiki.wxpython.org/WorkingWithImages
    # Could also see gdk_pixbuf_composite()
    def _RescaleImageOptimized(self, dc, im, scale, center):
        """Rescale an image considering it will be displayed on the buffer

        Does not modify the original image
        scale: the scale of the picture to fit the world
        center: position of the image in world coordinates
        return a tuple of
           * a copy of the image rescaled, it can be of any size
           * a 2-tuple representing the top-left point on the buffer coordinate
        """
        full_rect = self._GetImageRectOnBuffer(dc, im, scale, center)
        total_scale = scale * self.scale
        if total_scale == 1.0:
            # TODO: should see how to avoid (it slows down quite a bit)
            ret = im.Copy()
            tl = full_rect[0:2]
        elif total_scale < 1.0:
            # Scaling to values smaller than 1.0 was throwing exceptions
            w, h = full_rect[2:4]
            if w >= 1 and h >= 1:
                logging.debug("Scaling to %s, %s", w, h)
                ret = im.Scale(*full_rect[2:4])
                tl = full_rect[0:2]
            else:
                logging.warn("Illegal image scale %s, %s", w, h)
                return (None, None)
        elif total_scale > 1.0:
            # We could end-up with a lot of the up-scaling useless, so crop it
            orig_size = im.GetSize()
            # where is the buffer in the world?
            buffer_rect = (dc.DeviceToLogicalX(0),
                           dc.DeviceToLogicalY(0),
                           self.buffer_size[0],
                           self.buffer_size[1])
            goal_rect = wx.IntersectRect(full_rect, buffer_rect)
            if not goal_rect: # no intersection
                return (None, None)

            # where is this rect in the original image?
            unscaled_rect = ((goal_rect[0] - full_rect[0]) / total_scale,
                             (goal_rect[1] - full_rect[1]) / total_scale,
                             goal_rect[2] / total_scale,
                             goal_rect[3] / total_scale)
            # Note that width and length must be "double rounded" to account
            # for the round down of the origin and round up of the bottom left
            unscaled_rounded_rect = (
                int(unscaled_rect[0]), # rounding down
                int(unscaled_rect[1]),
                math.ceil(unscaled_rect[0] + unscaled_rect[2]) - int(unscaled_rect[0]),
                math.ceil(unscaled_rect[1] + unscaled_rect[3]) - int(unscaled_rect[1])
                )

            assert(unscaled_rounded_rect[0] + unscaled_rounded_rect[2] <= orig_size[0])
            assert(unscaled_rounded_rect[1] + unscaled_rounded_rect[3] <= orig_size[1])

            imcropped = im.GetSubImage(unscaled_rounded_rect)

            # like goal_rect but taking into account rounding
            final_rect = ((unscaled_rounded_rect[0] * total_scale) + full_rect[0],
                          (unscaled_rounded_rect[1] * total_scale) + full_rect[1],
                          int(unscaled_rounded_rect[2] * total_scale),
                          int(unscaled_rounded_rect[3] * total_scale))
            if (final_rect[2] > 2 * goal_rect[2] or
               final_rect[3] > 2 * goal_rect[3]):
                # a sign we went too far (too much zoomed) => not as perfect but don't use too much memory
                final_rect = goal_rect
                logging.debug("limiting image rescaling to %dx%d px" % final_rect[2:4])
            ret = imcropped.Rescale(*final_rect[2:4])
            # need to save it as the cropped part is not centred anymore
            tl = final_rect[0:2]
        return (ret, tl)

    def _GetImageRectOnBuffer(self, dc, im, scale, center):
        """
        Computes the rectangle containing the image on the buffer coordinates
        return rect (4-tuple of floats)
        """
        # There are two scales:
        # * the scale of the image (dependent on the size of what the image represent)
        # * the scale of the buffer (dependent on how much the user zoomed in)

        size = im.GetSize()

        actual_size = size[0] * scale, size[1] * scale
        tl_unscaled = (center[0] - (actual_size[0] / 2),
                       center[1] - (actual_size[1] / 2))
        tl = self.WorldToBufferPoint(tl_unscaled)
        final_size = (actual_size[0] * self.scale,
                      actual_size[1] * self.scale)
        return tl + final_size

    @staticmethod
    def memsetObject(bufferObject, value):
        "Note, dangerous"
        data = ctypes.POINTER(ctypes.c_char)()
        size = ctypes.c_int()
        ctypes.pythonapi.PyObject_AsCharBuffer(ctypes.py_object(bufferObject), ctypes.pointer(data), ctypes.pointer(size))
        ctypes.memset(data, value, size.value)

    def _DrawImageTransparentRescaled(self, dc, im, center, ratio=1.0, scale=1.0):
        """
        Draws one image with the given scale and opacity on the dc
        dc wx.DC
        im wx.Image
        center (2-tuple float)
        ratio (float)
        scale (float)
        """
        if ratio <= 0.0:
            return

        imscaled, tl = self._RescaleImageOptimized(dc, im, scale, center)
        if not imscaled:
            return

        if ratio < 1.0:
            # im2merged = im2scaled.AdjustChannels(1.0,1.0,1.0,ratio)
            # TODO Check if we could speed up by caching the alphabuffer
            abuf = imscaled.GetAlphaBuffer()
            self.memsetObject(abuf, int(255 * ratio))

        # TODO: the conversion from Image to Bitmap should be done only once,
        # after all the images are merged
        dc.DrawBitmapPoint(wx.BitmapFromImage(imscaled), tl)

    def _DrawMergedImages(self, dc, images, ratio = 0.5):
        """
        Draw the two images on the DC, centred around their _dc_center, with their own scale,
        and an opacity of "ratio" for im1.
        Both _dc_center's should be close in order to have the parts with only
        one picture drawn without transparency
        dc: wx.DC
        images (list of wx.Image): the images (it can also be None).
        ratio (0<float<1): how much to merge the images (between 1st and all other)
        scale (0<float): the scaling of the images in addition to their own
        Note: this is a very rough implementation. It's not fully optimized, and
        uses only a basic averaging algorithm.
        """
        t_start = time.time()

        # The idea:
        # * display the first image (SEM) last, with the given ratio (or 1 if it's the only one)
        # * display all the other images (fluo) as if they were average
        #   N images -> ratio = 1-0/N, 1-1/N,... 1-(N-1)/N

        # Fluo images to actually display (ie, remove None)
        fluo = [im for im in images[1:] if im is not None]
        nb_fluo = len(fluo)

        for i, im in enumerate(fluo): # display the fluo images first
            r = 1.0 - i / float(nb_fluo)
            self._DrawImageTransparentRescaled(dc, im, im._dc_center, r, scale=im._dc_scale)

        for im in images[:1]: # the first image (or nothing)
            if im is None:
                continue
            if nb_fluo == 0:
                ratio = 1.0 # no transparency if it's alone
            self._DrawImageTransparentRescaled(dc, im, im._dc_center, ratio, scale=im._dc_scale)

        t_now = time.time()
        fps = 1.0 / float(t_now - t_start)
        #logging.debug("Display speed: %s fps", fps)

    def WorldToBufferPoint(self, pos):
        """ Converts a position from world coordinates to buffer coordinates using
        the current values
        pos (2-tuple floats): the coordinates in the world
        """
        return WorldToBufferPoint(pos, self.world_pos_buffer, self.scale)

def WorldToBufferPoint(pos, world_pos, scale):
    """
    Converts a position from world coordinates to buffer coordinates
    pos (2-tuple floats): the coordinates in the world
    world_pos_buffer: the center of the buffer in world coordinates
    scale: how much zoomed is the buffer compared to the world
    """
    return (round((pos[0] - world_pos[0]) * scale),
            round((pos[1] - world_pos[1]) * scale))
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
