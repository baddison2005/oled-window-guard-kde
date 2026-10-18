"""Read-only application positions, eligibility and validated move/Restore destinations."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy
from .preview_data import drawing_preview


REASONS = {'excluded_app':'Excluded from movement', 'guard_owned':'OWG stays in place',
           'maximized':'Maximized', 'fullscreen':'Full screen', 'tiled':'Tiled',
           'immovable':'Window cannot be moved', 'spanning':'Outside one display’s usable area',
           'unselected_output':'Display not selected', 'modal':'Modal window',
           'transient':'Transient window', 'unsupported_window_type':'Unsupported window type'}


class MovementPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.application_names = {}
        self._hit_regions = []
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAccessibleName('Movement preview')

    def window_label(self, index):
        info = (self.data or {}).get('windowInfo', [])
        row = info[index] if index < len(info) else {}
        app = row.get('appId', '')
        name = self.application_names.get(app.lower().removesuffix('.desktop'), app or 'Application')
        eligibility = row.get('eligibility', 'unavailable')
        number = row.get('moveNumber', 0)
        status = 'Excluded' if eligibility == 'excluded' else 'Eligible' if eligibility == 'eligible' else 'Unavailable'
        detail = REASONS.get(row.get('reason'), 'Window state unavailable' if eligibility == 'unavailable' else
                             'Eligible by window settings; layout and safety checks determine the move')
        return name, status, detail, number

    def set_preview(self, value, output_id, application_names=None):
        try:
            self.data = drawing_preview(value, [output_id], len(value['destinations'])) if value else None
        except (ValueError, KeyError, TypeError):
            self.data = None
        self.application_names = application_names or {}
        descriptions = ['Application names and current positions. Stripes: excluded. Dashes: unavailable. Mint outlines: destinations.']
        if self.data:
            for index in range(len(self.data['windows'])):
                name,status,detail,number = self.window_label(index)
                descriptions.append(f"{name}: {status}"+(f", move {number}" if number else '')+f". {detail}.")
        self.setAccessibleDescription(' '.join(descriptions))
        self._hit_regions = []
        self.update()

    def mouseMoveEvent(self, event):
        point = event.position()
        self.setToolTip(next((text for region,text in reversed(self._hit_regions) if region.contains(point)), ''))
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        self._hit_regions = []
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.setBrush(self.palette().base())
        painter.drawRoundedRect(area, 12, 12)
        if not self.data:
            painter.setPen(self.palette().text().color())
            painter.drawText(area.adjusted(20, 12, -20, -12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             'Choose a display to see its windows.\nPreview safe moves to review destinations.')
            return
        geometry = self.data['geometry']
        scale = min((area.width()-28)/geometry['width'], (area.height()-42)/geometry['height'])
        width, height = geometry['width']*scale, geometry['height']*scale
        screen = QRectF(area.center().x()-width/2, area.y()+10+(area.height()-42-height)/2, width, height)
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.setBrush(self.palette().alternateBase())
        painter.drawRoundedRect(screen, 5, 5)
        painter.save()
        painter.setClipRect(screen)
        font = painter.font(); font.setPointSizeF(9); painter.setFont(font)
        def mapped(rect):
            return QRectF(screen.x()+(rect['x']-geometry['x'])*scale,
                          screen.y()+(rect['y']-geometry['y'])*scale,
                          rect['width']*scale, rect['height']*scale)
        for index, rect in enumerate(self.data['windows']):
            name,status,detail,number = self.window_label(index)
            box = mapped(rect)
            colour = QColor('#c69a53') if status == 'Excluded' else QColor('#6b9cba') if status == 'Eligible' else self.palette().mid().color()
            fill = QColor(colour); fill.setAlpha(45)
            painter.setPen(QPen(colour, 1, Qt.PenStyle.DashLine if status == 'Unavailable' else Qt.PenStyle.SolidLine))
            painter.setBrush(fill); painter.drawRoundedRect(box, 3, 3)
            if status == 'Excluded':
                stripe = QColor(colour); stripe.setAlpha(100)
                painter.setBrush(QBrush(stripe, Qt.BrushStyle.BDiagPattern)); painter.drawRoundedRect(box, 3, 3)
            label = f"{number}. {name}" if number else name
            text_area = box.adjusted(5, 3, -5, -3)
            if text_area.width() >= 25 and text_area.height() >= 14:
                painter.setPen(self.palette().text().color())
                painter.drawText(text_area, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(text_area.width())))
                if text_area.height() >= 32:
                    painter.drawText(text_area.adjusted(0, 16, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                     painter.fontMetrics().elidedText(status, Qt.TextElideMode.ElideRight, int(text_area.width())))
            self._hit_regions.append((box.intersected(screen), f"{name} — {status}. {detail}"+(f". Destination {number}" if number else '')))
        accent = QColor('#2fa88e')
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index, move in enumerate(self.data['destinations'], 1):
            before, after = mapped(move['from']), mapped(move['to'])
            painter.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            painter.drawLine(before.center(), after.center())
            painter.setPen(QPen(accent, 2)); painter.drawRoundedRect(after, 3, 3)
            painter.drawText(after.adjusted(2, 2, -4, -2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, str(index))
        painter.restore()
        font = painter.font(); font.setPointSizeF(8); painter.setFont(font)
        painter.setPen(self.palette().text().color())
        painter.drawText(area.adjusted(12, area.height()-25, -12, -4), Qt.AlignmentFlag.AlignCenter,
                         'Eligible  ·  Striped: excluded  ·  Dashed: unavailable  ·  Mint: destination')
