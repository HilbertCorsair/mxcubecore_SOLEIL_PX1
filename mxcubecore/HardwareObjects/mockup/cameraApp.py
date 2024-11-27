#!/usr/bin/python

version = 'v2.0'
"""
Created on Thu Oct  3 11:08:52 2019

@author: com-proxima2a / Damien JEANGEARD
         txo / Bixente Rey
"""

from PyQt4 import QtGui, QtCore
from PyQt4.uic import loadUi

import os
import sys
import time
import numpy as np

from redis_camera import camera

MAX_EDIT_TIME = 5 

class CameraWindow(QtGui.QMainWindow):

    def __init__(self, parent=None):
        super(CameraWindow, self).__init__(parent)

        self._w = loadUi(os.path.join(os.path.dirname(__file__), 'cameraApp.ui'))
        self.setCentralWidget(self._w)

        self.connectActions()
        
        self.camera = camera()
        self.x_dim = None
        self.y_dim = None

        self.last_image_id = 0

        self.statusBar().showMessage(version)

        self.cam_timer = QtCore.QTimer()
        self.cam_timer.timeout.connect(self.update_cam)
        self.cam_timer.start(50)

        self.pars_timer = QtCore.QTimer()
        self.pars_timer.timeout.connect(self.update_pars)
        self.pars_timer.start(1000)

        # init label_cam, QGraphicView
        #self.sceneCam = QtGui.QGraphicsScene()
        #self.label_cam.setScene(self.sceneCam)
        #self.label_cam.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Expanding)
        
        self.refreshMonitorList = np.array([time.time()])
        
        self.editing_exposure = False
        self.editing_exposure_time = None

        self.editing_gain = False
        self.editing_gain_time = None

    def update_cam(self):

        if None in [self.x_dim, self.y_dim]:
            self.x_dim = self.camera.x_pixels_in_detector
            self.y_dim = self.camera.y_pixels_in_detector
            print("Image dimensions are: %s x %s " % (self.x_dim, self.y_dim))

        try:
            image_id = self.camera.get_image_id()

            if self.last_image_id == image_id:
                 return

            self.image = self.camera.get_rgbimage() 
            self.last_image_id = image_id
            self.qimage = QtGui.QImage(self.image, self.x_dim, self.y_dim, \
                       QtGui.QImage.Format_RGB888)

            self.update_dimensions()

            w = self.canvas_w
            h = self.canvas_h

            pix = QtGui.QPixmap(self.qimage)
            pix = pix.scaled(w,h, QtCore.Qt.KeepAspectRatio)

            self._w.label_cam.setPixmap(pix)
            self.refresh_monitor()
        except KeyboardInterrupt:
            print("Quitting app")
            QtGui.qApp.exit()

    def update_pars(self):
        try:
            if not self.editing_gain:
                gain = int(self.camera.get_gain())
                self._w.slide_gain.setValue(gain)
                self._w.gain_ledit.setText(str(gain))
        except:
            print("Cannot update gain")

        exposure = self.camera.get_exposure()
        try:
            if not self.editing_exposure:
                #exposure = self.camera.get_exposure()
                #self._w.slide_exposure.setValue(exposure*1000)
                #self._w.slide_exposure.setValue(exposure)
                #self._w.exposure_ledit.setText(str(exposure))
                self._w.slide_exposure.setEnabled(True)
                self._w.exposure_ledit.setEnabled(True)
        except Exception, err:
            print "Exposure time: %s" % exposure
            print err
            #self._w.slide_exposure.setEnabled(False)
            #self._w.exposure_ledit.setEnabled(False)

        if self.editing_gain:
            if time.time() - self.editing_gain_time > MAX_EDIT_TIME:
                 self.editing_gain = False

        if self.editing_exposure:
            if time.time() - self.editing_exposure_time > MAX_EDIT_TIME:
                 self.editing_exposure = False

    def connectActions(self):

        self._w.exposure_ledit.returnPressed.connect(self.exposure_changed)
        self._w.exposure_ledit.textEdited.connect(self.exposure_edited)
        self._w.slide_exposure.sliderPressed.connect(self.exposure_edited)
        self._w.slide_exposure.sliderReleased.connect(self.exposure_slide_changed)

        self._w.gain_ledit.returnPressed.connect(self.gain_changed)
        self._w.gain_ledit.textEdited.connect(self.gain_edited)
        self._w.slide_gain.sliderPressed.connect(self.gain_edited)
        self._w.slide_gain.sliderReleased.connect(self.gain_slide_changed)

        self._w.pushButton_path.clicked.connect(self.openfolder)
        self._w.pushButton_save.clicked.connect(self.save_image)

    def save_image(self):
        if self._w.radioButton_jpg.isChecked() == True:
            ext = '.jpg'
            format = 'jpeg'
        elif self._w.radioButton_png.isChecked() == True:
            ext = '.png'
            format = 'png'
            
        date = time.strftime("%Y%b%d_%H%M%S",time.localtime())
        path = str(self._w.lineEdit_path.text())
        name = str(self._w.lineEdit_name.text()+date+ext)
        
        path = os.path.join(path,name)
        #pl.imshow(self.image, cmap='gray')
        #pl.imsave(path+'/'+name,self.image)
        self.qimage.save(path, format=format)
        #imsave(path+'/'+name, self.image)
        
    def openfolder(self):
        filePath = QtGui.QFileDialog.getExistingDirectory(self,'open Dir', QtCore.QDir.homePath())
        self._w.lineEdit_path.setText(filePath)
        
    def refresh_monitor(self):
        self.refreshMonitorList = np.append(self.refreshMonitorList,  time.time())
        
      # calcule de la moyenne de temps entre chaque images
        if self.refreshMonitorList.shape[0] == 20:
            t=[]
            for e,ti in enumerate(self.refreshMonitorList[:-1]):
                t.append( float( self.refreshMonitorList[e+1] - self.refreshMonitorList[e] ) )
            meanDeT = np.array(t).mean()
          # mise jour du 'showMessage'
            self.statusBar().showMessage('%s | %s Hz' % (version, str(int(1/meanDeT))))
            
            self.refreshMonitorList = np.array([time.time()])

    def update_dimensions(self):
        wApp = self.width()  # largeur de l'app
        hApp = self.height() # hauteur de l'app

        self._w.label_cam.setFixedSize( wApp-20, hApp-self._w.frame_2.height()-40 ) #assigne la taile de cam
        #self.framesizeCam = self._w.label_cam.frameSize().width(), self._w.label_cam.frameSize().height()
        self.canvas_w = self._w.label_cam.frameSize().width() 
        self.canvas_h = self._w.label_cam.frameSize().height()
        
        f2Geometry = self._w.frame_2.geometry() # recupere la taille et position de frame_2
        xFrame_2      = f2Geometry.x()       # position X Frame2
        yFrame_2      = f2Geometry.y()       # position Y Frame2
        widthFrame_2  = f2Geometry.width()   # 
        heightFrame_2 = f2Geometry.height()  #
        
        self._w.frame_2.setGeometry(xFrame_2, self._w.label_cam.frameSize().height()+10, widthFrame_2, heightFrame_2)
        
        f1Geometry = self._w.frame_1.geometry() # recupere la taille et position de frame_2
        xFrame_1      = f1Geometry.x()       # position X Frame2
        yFrame_1      = f1Geometry.y()       # position Y Frame2
        widthFrame_1  = f1Geometry.width()   # 
        heightFrame_1 = f1Geometry.height()  #
        
        self._w.frame_1.setGeometry(xFrame_1, self._w.label_cam.frameSize().height()+10, widthFrame_1, heightFrame_1)

    def exposure_changed(self):
        arg = float(self._w.exposure_ledit.text())
        self.camera.set_exposure(arg)

    def exposure_edited(self):
        self.editing_exposure = True
        self.editing_exposure_time = time.time()

    def exposure_slide_changed(self):
        arg = float(self._w.slide_exposure.value())/1000
        self.camera.set_exposure(arg)
        self._w.exposure_ledit.setText(str(arg))

    def gain_changed(self):
        arg = int(self._w.gain_ledit.text())
        self.camera.set_gain(arg)

    def gain_edited(self):
        print("gain edited")
        self.editing_gain = True
        self.editing_gain_time = time.time()

    def gain_slide_changed(self):
        arg = self._w.slide_gain.value()
        self.camera.set_gain(arg)
        self._w.gain_ledit.setText(str(arg))

    def get_exposure(self):
        self.camera.get_exposure()

    def get_gain(self):
        self.camera.get_exposure()

if __name__=='__main__':
    try:
        app = QtGui.QApplication([])
        win = CameraWindow()
        win.show()
        win.setWindowTitle("Prosilica Cam")
        win.setGeometry(100,100,900,840)
        app.exec_()
    except KeyboardInterrupt:
        sys.exit(0)

