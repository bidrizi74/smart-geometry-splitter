import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

from .smart_geometry_splitter_dialog import SmartGeometrySplitterDialog


class SmartGeometrySplitter:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")

        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = QIcon()

        self.action = QAction(
            icon,
            "Smart Geometry Splitter",
            self.iface.mainWindow()
        )

        self.action.setObjectName("SmartGeometrySplitterAction")
        self.action.setToolTip("Smart Geometry Splitter")
        self.action.setStatusTip(
            "Split polygons and lines directly in the active editable layer"
        )

        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(
            "&Smart Geometry Splitter",
            self.action
        )

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(
                "&Smart Geometry Splitter",
                self.action
            )

    def run(self):
        if self.dialog is None:
            self.dialog = SmartGeometrySplitterDialog(self.iface)

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()