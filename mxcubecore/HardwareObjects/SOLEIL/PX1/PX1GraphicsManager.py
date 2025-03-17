
from Qt4_GraphicsManager import Qt4_GraphicsManager
from QtImport import QApplication, QCursor, Qt

from XRayCentringArea import XRayCentringAreaItem
from GraphicsCursor import GraphicsCursor

import logging

log = logging.getLogger("HWR")

class PX1GraphicsManager(Qt4_GraphicsManager):

    phi_threshold = 1.0

    def __init__(self, *args):
        self.pixels_per_mm = None

        Qt4_GraphicsManager.__init__(self, *args)

        self.xraycent_area_item = None
        self.waiting_xraycent_area = False
        self.is_selecting_xraycent = False
        self.showing_xraycent = False

        self.xray_phipos = None
        self.video_mode = False

    def init(self):
        Qt4_GraphicsManager.init(self)
        self.cursor_item = GraphicsCursor(self)

        if self.camera_hwobj is not None:
            self.connect(self.camera_hwobj, "cameraChanged", self.camera_changed)

        if self.diffractometer_hwobj:
           self.connect(self.diffractometer_hwobj, "diffractometerMoved",
                         self.diffractometer_moved)

    def stop_select_xcentring_area(self):
        self.wait_xraycent_area = False
        self.is_selecting_xraycent = False
        QApplication.setOverrideCursor(QCursor(Qt.ArrowCursor))

    def end_xcentring(self):
        self.wait_xraycent_area = False
        self.is_selecting_xraycent = False
        self.centring_finished = True
        QApplication.setOverrideCursor(QCursor(Qt.ArrowCursor))

    def show_cursor(self, x, y):
        self.cursor_item.set_position(x,y)
        self.cursor_item.show()
        self.graphics_view.scene().addItem(self.cursor_item)
        self.graphics_view.scene().update()

    def hide_cursor(self):
        if self.cursor_item is not None:
            self.cursor_item.hide()
            self.graphics_view.scene().update()

    def set_centring_state(self,state):
        self.wait_xraycent_area = False
        self.is_selecting_xraycent = False
        self.hide_xraycent_area()
        Qt4_GraphicsManager.set_centring_state(self,state)

    def get_info_xcentring_area(self):
        if not self.xraycent_area_item: 
             return None

        cen = self.xraycent_area_item.get_center_coord()
        props = self.xraycent_area_item.get_properties()

        return cen,props

    def get_xcentring_nlines_nimgs(self):
        if not self.waiting_xraycent_area and self.xraycent_area_item is not None:
            return self.xraycent_area_item.get_rows_cols()
        else:
            return None

    def get_xcentring_deltas_start_end_mm(self):
        cent_x_pix = self.graphics_scene_size[0] / 2.0
        cent_y_pix = self.graphics_scene_size[1] / 2.0

        start_coords, end_coords = self.xraycent_area_item.get_start_end_coords() # pixels
       
        start_dx_pix = start_coords[0] - cent_x_pix
        start_dy_pix = start_coords[1] - cent_y_pix 

        end_dx_pix = end_coords[0] - cent_x_pix
        end_dy_pix = end_coords[1] - cent_y_pix

        start_dx_mm = -start_dx_pix / float(self.pixels_per_mm[0])
        start_dy_mm = start_dy_pix / float(self.pixels_per_mm[1])  # axe y opposite from grid to motor

        end_dx_mm = -end_dx_pix / float(self.pixels_per_mm[0])
        end_dy_mm = end_dy_pix / float(self.pixels_per_mm[1])

        extent_dx_pix = end_coords[0] - start_coords[0]
        extent_dy_pix = end_coords[1] - start_coords[1]

        extent_dx_mm = extent_dx_pix / float(self.pixels_per_mm[0])
        extent_dy_mm = extent_dy_pix / float(self.pixels_per_mm[1])

        return start_dx_mm, start_dy_mm, end_dx_mm, end_dy_mm, extent_dx_mm, extent_dy_mm
        
    def select_xcentring_area(self):
        if not self.waiting_xraycent_area:
            QApplication.setOverrideCursor(QCursor(Qt.BusyCursor))
            log.debug("Creating XRAY centring area. Beam_info=%s\n" % str(self.beam_info_dict))         
            if not self.xraycent_area_item:
                self.xraycent_area_item = XRayCentringAreaItem(self,
                     self.beam_info_dict, self.pixels_per_mm)
    
            ovly = self.xraycent_area_item.get_overlay()
            if ovly is not None:
                self.graphics_view.graphics_scene.removeItem(ovly)

            self.xraycent_area_item.set_draw_mode(True)
            self.xraycent_area_item.index = self.grid_count
            self.grid_count += 1
            self.graphics_view.graphics_scene.addItem(self.xraycent_area_item)
            self.waiting_xraycent_area = True

    def delete_xraycent_area(self):
        if self.xraycent_area_item is not None:
            self.delete_shape(self.xraycent_area_item)
            self.xraycent_area_item = None
            self.waiting_xraycent_area = False
            self.is_selecting_xraycent = False

    def mouse_clicked(self,x,y, left_click=True):
        if self.waiting_xraycent_area:
            self.is_selecting_xraycent = True
            self.xraycent_area_item.set_draw_mode(True)
            self.xraycent_area_item.set_start_position(x,y)
            self.show_xraycent_area()
            self.waiting_xraycent_area = False
        else: 
            Qt4_GraphicsManager.mouse_clicked(self,x,y,left_click)

    def mouse_released(self,x,y):
        if self.is_selecting_xraycent:
           self.xraycent_area_item.end_selection(x, y)
           self.xraycent_area_item.set_draw_mode(False)
           self.update_grid_motors()

        self.is_selecting_xraycent = False
        self.waiting_xraycent_area = False

        self.xray_phipos = self.diffractometer_hwobj.get_phi_position()

        Qt4_GraphicsManager.mouse_released(self,x,y)

    def mouse_moved(self, x, y):
        if self.is_selecting_xraycent:
            if self.xraycent_area_item.is_draw_mode():
                self.xraycent_area_item.set_end_position(x, y)
        else:
            Qt4_GraphicsManager.mouse_moved(self,x,y)
        
    def diffractometer_pixels_per_mm_changed(self, pixels_per_mm):
        # detect when zoom changed
        # log.debug("zoom changed")
        Qt4_GraphicsManager.diffractometer_pixels_per_mm_changed(self, pixels_per_mm)

        if pixels_per_mm != self.pixels_per_mm:
            log.debug("XRAY pixel_per_mm changed")
            self.update_xraycent_area()
            self.pixels_per_mm = pixels_per_mm

    def update_grid_motors(self):
        centx, centy = self.xraycent_area_item.get_center_coord()

        motor_pos = self.diffractometer_hwobj.get_centred_point_from_coord( \
                      centx, centy)

        center_coord = self.diffractometer_hwobj.motor_positions_to_screen(motor_pos)

        log.debug("update grid motors - before %s, %s - after calc %s, %s " % (centx,centy, center_coord[0], center_coord[1])) 
        self.xraycent_area_item.set_center_position(motor_pos)

        corner_pos = []
        for x,y in self.xraycent_area_item.get_corner_coord():
            motor_pos = self.diffractometer_hwobj.get_centred_point_from_coord(x,y)
            corner_pos.append(motor_pos)

        self.xraycent_area_item.set_corner_positions(corner_pos)

    def show_xraycent_area(self):
        if self.xraycent_area_item:
            self.xraycent_area_item.show()
            self.showing_xraycent = True 

    def hide_xraycent_area(self):
        if self.xraycent_area_item:
            self.xraycent_area_item.hide()
            self.showing_xraycent = False 

    def clear_all(self):
        self.hide_xraycent_area()
        Qt4_GraphicsManager.clear_all(self)
   
    def update_xraycent_area(self):
        griditem = self.xraycent_area_item

        if griditem is None:
            log.debug("no xraycent area yet")
            return

        grid_cpos = griditem.get_center_position()

        if grid_cpos is not None:
            center_coord = self.diffractometer_hwobj.\
                        motor_positions_to_screen(grid_cpos)

            cur_c = griditem.get_center_coord()

            if center_coord and cur_c is not None:
                if abs(cur_c[0] - center_coord[0]) >= 1 or \
                   abs(cur_c[1] - center_coord[1]) >= 1: 
                     #log.debug("manager setting xray center coord to %s" % str(center_coord))
                     griditem.set_center_coord(center_coord)

    def diffractometer_moved(self):
        self._show_xraycent_area()

    def camera_changed(self, camera):
        if camera != 'oav':
            self.video_mode = True
            self.hide_all_items()
            self.graphics_beam_item.hide()
            self.graphics_scale_item.hide()
        else:
            self.video_mode = False
            self.show_all_items()
            self.graphics_beam_item.show()
            self.graphics_scale_item.show()

    def show_all_items(self, diff_ready=None):
        if self.video_mode:
            self.hide_all_items()
            return 

        self._show_xraycent_area()

        Qt4_GraphicsManager.show_all_items(self)

    def hide_all_items(self, diff_ready=None):
        if diff_ready is None:
            diff_ready = self.diffractometer_hwobj.is_ready()  

        if self.xraycent_area_item and diff_ready: # keep xraycent_area if moving
            self.xraycent_area_item.hide()

        Qt4_GraphicsManager.hide_all_items(self)
         
    def _show_xraycent_area(self):
        if self.xraycent_area_item and self.showing_xraycent:
            phi_pos = self.diffractometer_hwobj.get_phi_position()  
            if None not in [phi_pos, self.xray_phipos]: 
                if abs(phi_pos - self.xray_phipos) < self.phi_threshold:
                    self.xraycent_area_item.show()
                    self.update_xraycent_area()
                else:
                    self.xraycent_area_item.hide()

    def set_xray_heatmap(self,filename):
        if self.xraycent_area_item:
            self.xraycent_area_item.set_overlay(filename)

