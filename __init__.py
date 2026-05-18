def classFactory(iface):
    from .smart_geometry_splitter import SmartGeometrySplitter
    return SmartGeometrySplitter(iface)