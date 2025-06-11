# -*- coding: utf-8 -*-

# Image Occlusion Enhanced Add-on for Anki
#
# Copyright (C) 2016-2020  Aristotelis P. <https://glutanimate.com/>
# Copyright (C) 2012-2015  Tiago Barroso <tmbb@campus.ul.pt>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version, with the additions
# listed at the end of the license file that accompanied this program.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# NOTE: This program is subject to certain additional terms pursuant to
# Section 7 of the GNU Affero General Public License.  You should have
# received a copy of these additional terms immediately following the
# terms and conditions of the GNU Affero General Public License that
# accompanied this program.
#
# If not, please request a copy through one of the means of contact
# listed here: <https://glutanimate.com/contact/>.
#
# Any modifications to this file must keep this entire header intact.

"""
Image Occlusion editor dialog
"""

import os, sys
addon_path = os.path.dirname(__file__)
sys.path.append(f"{os.path.dirname(__file__)}/vendor")

import json
import cv2 # OCR dependency
from anki.hooks import addHook, remHook
from aqt import deckchooser, mw, tagedit, webview
from aqt.qt import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QIcon,
    QKeySequence,
    QLabel,
    QMovie,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QSize,
    Qt,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    sip,
    pyqtSignal,
    QObject,     # For OCR Worker
    QThread,     # For OCR Thread
)
from aqt.utils import restoreGeom, saveGeom, askUser, tooltip, showWarning

from .config import *
from .consts import *
from .dialogs import ioHelp
from .lang import _


# (Placed near the top of the file, after the imports)
def get_box_coords(box_points):
    """ Get the min/max coordinates for a bounding box defined by 4 points. """
    x_coords = [p[0] for p in box_points]
    y_coords = [p[1] for p in box_points]
    return min(x_coords), min(y_coords), max(x_coords), max(y_coords)

def calculate_intersection(box_a_coords, box_b_coords):
    """Calculate the intersection area of two bounding boxes."""
    ax_min, ay_min, ax_max, ay_max = box_a_coords
    bx_min, by_min, bx_max, by_max = box_b_coords

    # Determine the coordinates of the intersection rectangle
    inter_x_min = max(ax_min, bx_min)
    inter_y_min = max(ay_min, by_min)
    inter_x_max = min(ax_max, bx_max)
    inter_y_max = min(ay_max, by_max)

    # Calculate the area of intersection
    inter_width = max(0, inter_x_max - inter_x_min)
    inter_height = max(0, inter_y_max - inter_y_min)
    return inter_width * inter_height

def do_boxes_overlap(box_a, box_b, threshold=0.1):
    """
    Check if two bounding boxes overlap by at least a certain threshold.
    The threshold is the percentage of the smaller box's area that must be covered by the intersection.
    """
    box_a_coords = get_box_coords(box_a)
    box_b_coords = get_box_coords(box_b)

    intersection_area = calculate_intersection(box_a_coords, box_b_coords)

    if intersection_area == 0:
        return False

    # Calculate the area of each box
    area_a = (box_a_coords[2] - box_a_coords[0]) * (box_a_coords[3] - box_a_coords[1])
    area_b = (box_b_coords[2] - box_b_coords[0]) * (box_b_coords[3] - box_b_coords[1])

    # Check if the intersection is greater than the threshold percentage of the SMALLEST box
    # This is more "forgiving" as it helps merge small words into larger blocks.
    smaller_area = min(area_a, area_b)
    if smaller_area == 0:
        return True # Overlap exists if one box has no area but intersection is non-zero

    return (intersection_area / smaller_area) > threshold


class OcrWorker(QObject):
    """
    Runs the EasyOCR process in a separate thread to avoid freezing the UI.
    Optimized for CPU performance and provides more controllable box merging.
    """
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, image_path: str, reader):
        super().__init__()
        self.image_path = image_path
        self.reader = reader

    def run(self):
        """
        The main OCR logic. Reads an image, detects text, merges boxes,
        and emits the results.
        """
        try:
            if not self.image_path:
                self.error.emit("No image path provided.")
                return

            if self.reader is None:
                self.error.emit("EasyOCR reader not initialized.")
                return

            img = cv2.imread(self.image_path)
            if img is None:
                self.error.emit(f"Could not load image from path: {self.image_path}.")
                return

            results = self.reader.readtext(
                self.image_path,
                paragraph=False,
                batch_size=16
            )

            if not results:
                self.finished.emit([]) # Emit empty list for no text found
                return

            # Filter results by a confidence threshold
            bounding_boxes = [r[0] for r in results if r[2] > 0.60]

            OVERLAP_THRESHOLD = 0.1

            merged_boxes = []
            if not bounding_boxes:
                self.finished.emit([])
                return
                
            unmerged_indices = list(range(len(bounding_boxes)))

            while unmerged_indices:
                base_idx = unmerged_indices.pop(0)
                current_cluster_bbox = bounding_boxes[base_idx]
                
                i = 0
                while i < len(unmerged_indices):
                    compare_idx = unmerged_indices[i]
                    
                    if do_boxes_overlap(current_cluster_bbox, bounding_boxes[compare_idx], threshold=OVERLAP_THRESHOLD):
                        # Merge the overlapping box into the current cluster
                        x_min_curr, y_min_curr, x_max_curr, y_max_curr = get_box_coords(current_cluster_bbox)
                        x_min_other, y_min_other, x_max_other, y_max_other = get_box_coords(bounding_boxes[compare_idx])

                        new_min_x = min(x_min_curr, x_min_other)
                        new_min_y = min(y_min_curr, y_min_other)
                        new_max_x = max(x_max_curr, x_max_other)
                        new_max_y = max(y_max_curr, y_max_other)

                        current_cluster_bbox = [[new_min_x, new_min_y], [new_max_x, new_min_y],
                                                [new_max_x, new_max_y], [new_min_x, new_max_y]]
                        
                        unmerged_indices.pop(i)
                        # Restart the scan for this cluster to check against all other boxes
                        i = 0
                    else:
                        i += 1
                
                merged_boxes.append(current_cluster_bbox)
            # --- End Merging Logic ---

            rects_to_draw = []
            for bbox in merged_boxes:
                x_min, y_min, x_max, y_max = get_box_coords(bbox)
                rects_to_draw.append({
                    "x": int(x_min), "y": int(y_min),
                    "w": int(x_max - x_min), "h": int(y_max - y_min)
                })
            
            self.finished.emit(rects_to_draw)

        except Exception as e:
            import traceback
            self.error.emit(f"An error occurred during text recognition: {traceback.format_exc()}")


class ImgOccWebPage(webview.AnkiWebPage):
    def acceptNavigationRequest(self, url, navType, isMainFrame):
        return True


class ImgOccWebView(webview.AnkiWebView):

    escape_pressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._domDone = False

    def _onBridgeCmd(self, cmd):
        if sip.isdeleted(self):
            return

        if cmd == "domDone":
            return

        if cmd == "svgEditDone":
            self._domDone = True
            self._maybeRunActions()
        else:
            return self.onBridgeCmd(cmd)

    def runOnLoaded(self, callback):
        self._domDone = False
        self._queueAction("callback", callback)

    def _maybeRunActions(self):
        while self._pendingActions and self._domDone:
            name, args = self._pendingActions.pop(0)

            if name == "eval":
                self._evalWithCallback(*args)
            elif name == "setHtml":
                self._setHtml(*args)
            elif name == "callback":
                callback = args[0]
                callback()
            else:
                raise Exception(
                    _("unknown action: {action_name}").format(action_name=name)
                )

    def onEsc(self):
        self.escape_pressed.emit()


class ImgOccEdit(QDialog):
    """Main Image Occlusion Editor dialog"""

    def __init__(self, imgoccadd, parent, image_path: str, ocr_reader):
        QDialog.__init__(self)
        mw.setupDialogGC(self)
        self.setWindowFlags(Qt.WindowType.Window)
        self.visible = False
        self.imgoccadd = imgoccadd
        self.parent = parent
        self.mode = "add"
        self.ocr_master_results = None
        loadConfig(self)
        self.setupUi()
        restoreGeom(self, "imgoccedit")

        
        # Start OCR in the background
        self._start_ocr_thread(image_path, ocr_reader)

        try:
            from aqt.gui_hooks import profile_will_close
            profile_will_close.append(self.onProfileUnload)
        except (ImportError, ModuleNotFoundError):
            addHook("unloadProfile", self.onProfileUnload)

    def _start_ocr_thread(self, image_path, ocr_reader):
        """Initializes and starts the OCR worker thread."""
        self.thread = QThread()
        self.worker = OcrWorker(image_path, ocr_reader)
        self.worker.moveToThread(self.thread)

        # Connect signals and slots
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_ocr_finished)
        self.worker.error.connect(self._on_ocr_error)
        
        # Clean up the thread
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        # Start the thread
        self.thread.start()

    def _on_ocr_finished(self, rects: list):
        """
        Slot to receive OCR results from the worker thread.
        Stores the results locally and sends them to the JavaScript front-end
        for the magic wand tool.
        """
        self.ocr_master_results = rects
        self._send_ocr_data_to_js(rects)
        tooltip("OCR processing complete. Use 'Auto Cover' or the Magic Wand tool.")
    
    def _send_ocr_data_to_js(self, rects: list):
        """Sends the OCR data to the webview to be stored in JavaScript."""
        if not self.svg_edit or not rects:
            return
        rects_json = json.dumps(rects)
        # This calls the new JavaScript function to ONLY store the data
        js_command = f"svgCanvas.storeOcrResults({rects_json});"
        self.svg_edit.eval(js_command)


    def _on_ocr_error(self, error_msg: str):
        """Slot to receive errors from the OcrWorker."""
        showWarning(f"OCR Error: {error_msg}")
        self.ocr_master_results = []

    def closeEvent(self, event):
        if mw.pm.profile is not None:
            self.deckChooser.cleanup()
            saveGeom(self, "imgoccedit")
        self.visible = False
        self.svg_edit = None
        del self.svg_edit_anim
        try:
            from aqt.gui_hooks import profile_will_close
            profile_will_close.append(self.onProfileUnload)
        except (ImportError, ModuleNotFoundError):
            remHook("unloadProfile", self.onProfileUnload)
        QDialog.reject(self)

    def onProfileUnload(self):
        if not sip.isdeleted(self):
            self.close()

    def reject(self):
        if not self.svg_edit:
            return super().reject()
        self.svg_edit.evalWithCallback(
            "svgCanvas.undoMgr.getUndoStackSize() == 0", self._on_reject_callback
        )

    def _on_reject_callback(self, undo_stack_empty: bool):
        if (undo_stack_empty and not self._input_modified()) or askUser(
            "Are you sure you want to close the window? This will discard any unsaved"
            " changes.",
            title="Exit Image Occlusion?",
        ):
            return super().reject()

    def _input_modified(self) -> bool:
        tags_modified = self.tags_edit.isModified()
        fields_modified = any(
            plain_text_edit.document().isModified()  # type: ignore
            for plain_text_edit in self.findChildren(QPlainTextEdit)
        )
        return tags_modified or fields_modified

    def setupUi(self):
        """Set up ImgOccEdit UI"""
        self.svg_edit = ImgOccWebView(parent=self)
        self.svg_edit._page = ImgOccWebPage(self.svg_edit._onBridgeCmd)
        self.svg_edit.setPage(self.svg_edit._page)
        self.svg_edit.escape_pressed.connect(self.reject)

        # ... (rest of setupUi is unchanged) ...
        self.tags_hbox = QHBoxLayout()
        self.tags_edit = tagedit.TagEdit(self)
        self.tags_label = QLabel(_("Tags"))
        self.tags_label.setFixedWidth(70)
        self.deck_container = QWidget()
        self.deckChooser = deckchooser.DeckChooser(mw, self.deck_container, label=True)
        self.deckChooser.deck.setAutoDefault(False)
        if self.deck_container.layout().children():
            for i in range(self.deck_container.layout().children()[0].count()):
                try:
                    item = self.deck_container.layout().children()[0].itemAt(i)
                    item.widget().setFocusPolicy(Qt.FocusPolicy.ClickFocus)
                    item.widget().setAutoDefault(False)
                except AttributeError:
                    pass
        self.bottom_label = QLabel()
        button_box = QDialogButtonBox(Qt.Orientation.Horizontal, self)
        button_box.setCenterButtons(False)
        self.ocr_btn = QPushButton(_("Auto &Cover"))
        self.ocr_btn.setToolTip(_("Automatically detect and cover text in the image"))
        self.ocr_btn.clicked.connect(self.runAutomatedCover)
        self.ocr_btn.setAutoDefault(False)
        self.ocr_btn.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        image_btn = QPushButton(_("Change &Image"))
        image_btn.clicked.connect(self.changeImage)
        image_btn.setIcon(QIcon(os.path.join(ICONS_PATH, "add.png")))
        image_btn.setIconSize(QSize(16, 16))
        image_btn.setAutoDefault(False)
        help_btn = QPushButton(_("&Help"))
        help_btn.clicked.connect(self.onHelp)
        help_btn.setAutoDefault(False)
        self.occl_tp_select = QComboBox()
        self.occl_tp_select.addItem(_("Don't Change"), "Don't Change")
        self.occl_tp_select.addItem(_("Hide All, Guess One"), "Hide All, Guess One")
        self.occl_tp_select.addItem(_("Hide One, Guess One"), "Hide One, Guess One")
        self.edit_btn = button_box.addButton(_("&Edit Cards"), QDialogButtonBox.ButtonRole.ActionRole)
        self.new_btn = button_box.addButton(_("&Add New Cards"), QDialogButtonBox.ButtonRole.ActionRole)
        self.ao_btn = button_box.addButton(_("Hide &All, Guess One"), QDialogButtonBox.ButtonRole.ActionRole)
        self.oa_btn = button_box.addButton(_("Hide &One, Guess One"), QDialogButtonBox.ButtonRole.ActionRole)
        close_button = button_box.addButton(_("&Close"), QDialogButtonBox.ButtonRole.RejectRole)
        image_tt = _("Switch to a different image while preserving all of the shapes and fields")
        dc_tt = _("Preserve existing occlusion type")
        edit_tt = _("Edit all cards using current mask shapes and field entries")
        new_tt = _("Create new batch of cards without editing existing ones")
        ao_tt = _("Generate cards with nonoverlapping information, where all<br>labels are hidden on the front and one revealed on the back")
        oa_tt = _("Generate cards with overlapping information, where one<br>label is hidden on the front and revealed on the back")
        close_tt = _("Close Image Occlusion Editor without generating cards")
        image_btn.setToolTip(image_tt)
        self.edit_btn.setToolTip(edit_tt)
        self.new_btn.setToolTip(new_tt)
        self.ao_btn.setToolTip(ao_tt)
        self.oa_btn.setToolTip(oa_tt)
        close_button.setToolTip(close_tt)
        self.occl_tp_select.setItemData(0, dc_tt, Qt.ItemDataRole.ToolTipRole)
        self.occl_tp_select.setItemData(1, ao_tt, Qt.ItemDataRole.ToolTipRole)
        self.occl_tp_select.setItemData(2, oa_tt, Qt.ItemDataRole.ToolTipRole)
        for btn in [image_btn, self.edit_btn, self.new_btn, self.ao_btn, self.oa_btn, close_button]:
            btn.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.edit_btn.clicked.connect(self.editNote)
        self.new_btn.clicked.connect(self.new)
        self.ao_btn.clicked.connect(self.addAO)
        self.oa_btn.clicked.connect(self.addOA)
        close_button.clicked.connect(self.close)
        bottom_hbox = QHBoxLayout()
        bottom_hbox.addWidget(image_btn)
        bottom_hbox.addWidget(help_btn)
        bottom_hbox.insertStretch(2, stretch=1)
        bottom_hbox.addWidget(self.ocr_btn)
        bottom_hbox.addWidget(self.bottom_label)
        bottom_hbox.addWidget(self.occl_tp_select)
        bottom_hbox.addWidget(button_box)
        vbox1 = QVBoxLayout()
        svg_edit_loader = QLabel(_("Loading..."))
        svg_edit_loader.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loader_icon = os.path.join(ICONS_PATH, "loader.gif")
        anim = QMovie(loader_icon)
        svg_edit_loader.setMovie(anim)
        anim.start()
        self.svg_edit_loader = svg_edit_loader
        self.svg_edit_anim = anim
        vbox1.addWidget(self.svg_edit, stretch=1)
        vbox1.addWidget(self.svg_edit_loader, stretch=1)
        self.vbox2 = QVBoxLayout()
        tab1 = QWidget()
        self.tab2 = QWidget()
        tab1.setLayout(vbox1)
        self.tab2.setLayout(self.vbox2)
        self.tab_widget = QTabWidget()
        self.tab_widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.tab_widget.addTab(tab1, _("&Masks Editor"))
        self.tab_widget.addTab(self.tab2, _("&Fields"))
        self.tab_widget.setTabToolTip(1, _("Include additional information (optional)"))
        self.tab_widget.setTabToolTip(0, _("Create image occlusion masks (required)"))
        vbox_main = QVBoxLayout()
        vbox_main.addWidget(self.tab_widget)
        vbox_main.addLayout(bottom_hbox)
        self.setLayout(vbox_main)
        self.setMinimumWidth(640)
        self.tab_widget.setCurrentIndex(0)
        self.svg_edit.setFocus()
        self.showSvgEdit(False)
        for i in range(1, 10):
            QShortcut(QKeySequence("Ctrl+%i" % i), self).activated.connect(lambda f=i - 1: self.focusField(f))
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(lambda: self.defaultAction(True))
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self).activated.connect(lambda: self.addOA(True))
        QShortcut(QKeySequence("Ctrl+Tab"), self).activated.connect(self.switchTabs)
        QShortcut(QKeySequence("Ctrl+r"), self).activated.connect(self.resetMainFields)
        QShortcut(QKeySequence("Ctrl+Shift+r"), self).activated.connect(self.resetAllFields)
        QShortcut(QKeySequence("Ctrl+Shift+t"), self).activated.connect(self.focusTags)
        QShortcut(QKeySequence("Ctrl+f"), self).activated.connect(self.fitImageCanvas)
        QShortcut(QKeySequence("c"), self).activated.connect(self.runAutomatedCover)


    def runAutomatedCover(self):
        """
        Uses pre-processed and stored OCR data to draw rectangles on the SVG canvas.
        This is triggered by the "Auto Cover" button.
        """
        if self.ocr_master_results is None:
            tooltip("Still processing image, please wait...")
            return

        if not self.ocr_master_results:
            tooltip("No text was detected in the image.")
            return

        # Draw the rectangles from the master list
        self.drawOcrRects(self.ocr_master_results)
        tooltip(f"Added {len(self.ocr_master_results)} occlusion masks.")


    def drawOcrRects(self, rects: list):
        if not self.svg_edit:
            return
        rects_json = json.dumps(rects)
        js_command = f"""
        (function() {{
            var svgCanvas = window.svgCanvas;
            if (!svgCanvas) {{
                console.error("svgCanvas object not found in drawOcrRects JS.");
                return;
            }}
            // Call the JavaScript function that draws the provided rects
            svgCanvas.drawOcrRects({rects_json});
        }})();
        """
        self.svg_edit.eval(js_command)

    # ... (the rest of the ImgOccEdit class remains unchanged)
    def changeImage(self):
        self.imgoccadd.onChangeImage()
        self.fitImageCanvas()
        self.fitImageCanvas(delay=100)

    def defaultAction(self, close):
        if self.mode == "add":
            self.addAO(close)
        else:
            self.editNote()

    def addAO(self, close=False):
        self.imgoccadd.onAddNotesButton("ao", close)

    def addOA(self, close=False):
        self.imgoccadd.onAddNotesButton("oa", close)

    def new(self, close=False):
        choice = self.occl_tp_select.currentData()
        self.imgoccadd.onAddNotesButton(choice, close)

    def editNote(self):
        choice = self.occl_tp_select.currentData()
        self.imgoccadd.onEditNotesButton(choice)

    def onHelp(self):
        if self.mode == "add":
            ioHelp("add", parent=self)
        else:
            ioHelp("edit", parent=self)

    def resetFields(self):
        layout = self.vbox2
        for i in reversed(list(range(layout.count()))):
            item = layout.takeAt(i)
            layout.removeItem(item)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                sublayout = item.layout()
                sublayout.setParent(None)
                for i in reversed(list(range(sublayout.count()))):
                    subitem = sublayout.takeAt(i)
                    sublayout.removeItem(subitem)
                    subitem.widget().setParent(None)
        self.tags_hbox.setParent(None)

    def setupFields(self, flds):
        self.tedit = {}
        self.tlabel = {}
        self.flds = flds
        for i in flds:
            if i["name"] in self.ioflds_priv:
                continue
            hbox = QHBoxLayout()
            tedit = QPlainTextEdit()
            label = QLabel(i["name"])
            hbox.addWidget(label)
            hbox.addWidget(tedit)
            tedit.setTabChangesFocus(True)
            tedit.setMinimumHeight(40)
            label.setFixedWidth(70)
            self.tedit[i["name"]] = tedit
            self.tlabel[i["name"]] = label
            self.vbox2.addLayout(hbox)
        self.tags_hbox.addWidget(self.tags_label)
        self.tags_hbox.addWidget(self.tags_edit)
        self.vbox2.addLayout(self.tags_hbox)
        self.vbox2.addWidget(self.deck_container)
        self.tab2.setTabOrder(self.tags_edit, self.deckChooser.deck)

    def switchToMode(self, mode):
        hide_on_add = [self.occl_tp_select, self.edit_btn, self.new_btn]
        hide_on_edit = [self.ao_btn, self.oa_btn]
        self.mode = mode
        for i in list(self.tedit.values()):
            i.show()
        for i in list(self.tlabel.values()):
            i.show()
        if mode == "add":
            for i in hide_on_add:
                i.hide()
            for i in hide_on_edit:
                i.show()
            dl_txt = _("Deck")
            ttl = _("Image Occlusion Enhanced - Add Mode")
            bl_txt = _("Add Cards:")
        else:
            for i in hide_on_add:
                i.show()
            for i in hide_on_edit:
                i.hide()
            for i in self.sconf["skip"]:
                if i in list(self.tedit.keys()):
                    self.tedit[i].hide()
                    self.tlabel[i].hide()
            dl_txt = _("Deck for <i>Add new cards</i>")
            ttl = _("Image Occlusion Enhanced - Editing Mode")
            bl_txt = _("Type:")
        self.deckChooser.deckLabel.setText(dl_txt)
        self.setWindowTitle(ttl)
        self.bottom_label.setText(bl_txt)

    def showSvgEdit(self, state):
        if not state:
            self.svg_edit.hide()
            self.svg_edit_anim.start()
            self.svg_edit_loader.show()
        else:
            self.svg_edit_anim.stop()
            self.svg_edit_loader.hide()
            self.svg_edit.show()

    def switchTabs(self):
        currentTab = self.tab_widget.currentIndex()
        if currentTab == 0:
            self.tab_widget.setCurrentIndex(1)
            if isinstance(QApplication.focusWidget(), QPushButton):
                self.tedit[self.ioflds["hd"]].setFocus()
        else:
            self.tab_widget.setCurrentIndex(0)

    def focusField(self, idx):
        self.tab_widget.setCurrentIndex(1)
        target_item = self.vbox2.itemAt(idx)
        if not target_item:
            return
        target_layout = target_item.layout()
        target_widget = target_item.widget()
        if target_layout:
            target = target_layout.itemAt(1).widget()
        elif target_widget:
            target = target_widget
        target.setFocus()

    def focusTags(self):
        self.tab_widget.setCurrentIndex(1)
        self.tags_edit.setFocus()

    def resetMainFields(self):
        for i in self.flds:
            fn = i["name"]
            if fn in self.ioflds_priv or fn in self.ioflds_prsv:
                continue
            self.tedit[fn].setPlainText("")

    def resetAllFields(self):
        self.resetMainFields()
        for i in self.ioflds_prsv:
            self.tedit[i].setPlainText("")

    def fitImageCanvas(self, delay: int = 5):
        self.svg_edit.eval(
            f"""
setTimeout(function(){{
    svgCanvas.zoomChanged('', 'canvas');
}}, {delay})
"""
        )