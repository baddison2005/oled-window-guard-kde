"""A slider with an editable numeric value in the setting's display units."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QSlider, QDoubleSpinBox


class CompactDoubleSpinBox(QDoubleSpinBox):
    def textFromValue(self, value):
        if value == int(value):
            return self.locale().toString(int(value))
        return super().textFromValue(value)


class SliderControl(QWidget):
    valueChanged = Signal(float)

    def __init__(self, spec, value, *, multiplier=1, suffix='', step=1, parent=None):
        super().__init__(parent)
        self.multiplier = multiplier
        self.step = step
        self._updating = False
        self._value = None
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(spec['minimum']/step), round(spec['maximum']/step))
        self.slider.setTracking(False)
        self.number = CompactDoubleSpinBox()
        self.number.setRange(spec['minimum']*multiplier, spec['maximum']*multiplier)
        self.number.setDecimals(1 if multiplier == 100 else (1 if step == .5 else 2))
        self.number.setSingleStep(step*multiplier)
        self.number.setSuffix(suffix)
        self.number.setKeyboardTracking(False)
        self.number.setMinimumWidth(100)
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.slider, 1); layout.addWidget(self.number)
        self.setValue(value)
        self.slider.valueChanged.connect(lambda tick: self.setValue(tick*step))
        self.number.valueChanged.connect(lambda value: self.setValue(value/multiplier))
        self.setFocusProxy(self.slider)

    def value(self):
        return self._value

    def setValue(self, value):
        if self._updating:
            return
        previous = self.value()
        self._updating = True
        try:
            if self.step == .5:
                value = round(value/.5)*.5
            self.number.setValue(value*self.multiplier)
            self._value = self.number.value()/self.multiplier
            self.slider.setValue(round(self._value/self.step))
        finally:
            self._updating = False
        if previous != self.value():
            self.valueChanged.emit(self.value())

    def setAccessibleName(self, name):
        super().setAccessibleName(name)
        self.slider.setAccessibleName(name)
        self.number.setAccessibleName(name + ' value')
