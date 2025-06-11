/*
 * ext-image-occlusion.js
 *
 * Licensed under the GNU AGPLv3
 *
 * Copyright(c) 2012-2015 tmbb
 * Copyright(c) 2016-2017 Glutanate
 *
 * This file is part of Image Occlusion Enhanced for Anki
 *
 */

// Pass in `S` as an argument to access the SVG canvas's private methods.
svgEditor.addExtension("Image Occlusion (Anki)", function(S) {
    
    // This function runs once the editor is fully ready.
    svgEditor.ready(function () {
        // Alias the private methods from the `S` object for convenience.
        var svgCanvas = svgEditor.canvas;
        var addCommandToHistory = S.addCommandToHistory;
        var BatchCommand = S.BatchCommand;
        var InsertElementCommand = S.InsertElementCommand;
        var call = S.call;

        // This sends a signal back to Python that SVG-Edit is ready.
        pycmd("svgEditDone");
        svgCanvas.storedOcrRects = []; // This will hold the data from Python

        /**
         * @function finalizeCreation
         * @param {Array<SVGElement>} new_elems - An array of newly created SVG elements.
         * @param {string} name - A descriptive name for the undo/redo action.
         * This helper function correctly registers a batch of new elements with the
         * editor's history system, making their creation a single, undoable action.
         */
        function finalizeCreation(new_elems, name) {
            if (!new_elems || new_elems.length === 0) return;
            
            name = name || "Create Occlusion Masks";
            
            var batchCmd = new BatchCommand(name);

            // --- FIXED LOGIC ---
            // Iterate backwards through the newly created elements.
            // This ensures that when redoing, the `nextSibling` of each element
            // will already have been re-inserted into the DOM.
            for (var i = new_elems.length - 1; i >= 0; i--) {
                var elem = new_elems[i];
                batchCmd.addSubCommand(new InsertElementCommand(elem));
            }
            
            if (!batchCmd.isEmpty()) {
                // Add the entire batch command to the history stack.
                addCommandToHistory(batchCmd);
                // Trigger the 'changed' event to update the editor's UI.
                call("changed", new_elems);
            }
        };

        /**
         * @function storeOcrResults
         * @param {Array<Object>} rects - An array of rectangle data objects from Python.
         * This function stores the OCR results for later use.
         */
        svgCanvas.storeOcrResults = function(rects) {
            svgCanvas.storedOcrRects = rects;
        };

        /**
         * @function drawOcrRects
         * @param {Array<Object>} rects - An array of rectangle data objects.
         * This function draws all provided rectangles, making the action undoable.
         * Used by the "Auto Cover" button.
         */
        svgCanvas.drawOcrRects = function(rects) {
            if (!rects || rects.length === 0) return;

            var new_elems = [];
            rects.forEach(function(r) {
                var newElement = svgCanvas.addSvgElementFromJson({
                    element: "rect",
                    attr: {
                        x: r.x, y: r.y, width: r.w, height: r.h,
                        fill: svgCanvas.getColor('fill'),
                        stroke: svgCanvas.getColor('stroke'),
                        'stroke-width': svgCanvas.getStrokeWidth(),
                        id: svgCanvas.getNextId()
                    }
                });
                new_elems.push(newElement);
            });
            
            // Finalize all newly created elements, making the entire action undoable.
            finalizeCreation(new_elems, "Create All Occlusion Masks");
        };

        /**
         * Selects and draws OCR rectangles that intersect with a given selection box.
         * The creation of these rectangles is registered as a single, undoable action.
         * This is used by your "magic" tool.
         *
         * @param {object} selectionBox - An object with x, y, width, and height properties.
         */
        svgCanvas.drawOcrRectsInSelection = function(selectionBox) {
            var ocrRects = svgCanvas.storedOcrRects;
            if (!ocrRects || ocrRects.length === 0) return;

            var rectsToDraw = [];

            var selX1 = selectionBox.x;
            var selY1 = selectionBox.y;
            var selX2 = selectionBox.x + selectionBox.width;
            var selY2 = selectionBox.y + selectionBox.height;

            ocrRects.forEach(function(ocrRect) {
                var ocrX1 = ocrRect.x;
                var ocrY1 = ocrRect.y;
                var ocrX2 = ocrRect.x + ocrRect.w;
                var ocrY2 = ocrRect.y + ocrRect.h;

                // Check if the selection box and the OCR rectangle intersect.
                var intersects = (selX1 < ocrX2 && selX2 > ocrX1 &&
                                selY1 < ocrY2 && selY2 > ocrY1);

                if (intersects) {
                    rectsToDraw.push(ocrRect);
                }
            });

            if (rectsToDraw.length > 0) {
                var new_elems = [];
                rectsToDraw.forEach(function(r) {
                    var newElement = svgCanvas.addSvgElementFromJson({
                        element: "rect",
                        attr: {
                            x: r.x, y: r.y, width: r.w, height: r.h,
                            fill: svgCanvas.getColor('fill'),
                            stroke: svgCanvas.getColor('stroke'),
                            'stroke-width': svgCanvas.getStrokeWidth(),
                            id: svgCanvas.getNextId()
                        }
                    });
                    new_elems.push(newElement);
                });
                // Finalize the created elements, making the action undoable.
                finalizeCreation(new_elems, "Create Occlusion Masks in Selection");
            }
        };
    });

    // This part defines the extension's properties and buttons for the UI.
     return {
        name: "Image Occlusion",
        svgicons: "extensions/image-occlusion-icon.xml",
        buttons: [{
          id: "set_zoom_canvas",
          type: "mode",
          title: "Fit image to canvas",
          key: "F",
          events: {
            "click": function() {
              svgCanvas.zoomChanged('', 'canvas');
            }
          }
        }],
    };
});