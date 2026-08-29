// mt_clip170 pipeline — headless Fiji macro.
// Driven by driver.py with a single argument string:
//     image=ABSPATH;out_dir=ABSPATH;params_path=ABSPATH;stages=A-B
//
// Reads parameters from a JSON-lite file written by driver.py.
// Stages are implemented incrementally; unimplemented stages log "STUB".

setBatchMode(true);

// ============================================================
// ── arg parsing ──────────────────────────────────────────────
// ============================================================
argStr = getArgument();
if (lengthOf(argStr) == 0) {
    exit("no args — expected image=...;out_dir=...;params_path=...;stages=A-B");
}

// simple k=v; parser
function argGet(s, key) {
    tag = key + "=";
    i = indexOf(s, tag);
    if (i < 0) return "";
    i = i + lengthOf(tag);
    j = indexOf(s, ";", i);
    if (j < 0) j = lengthOf(s);
    return substring(s, i, j);
}

IMAGE_PATH   = argGet(argStr, "image");
OUT_DIR      = argGet(argStr, "out_dir");
PARAMS_PATH  = argGet(argStr, "params_path");
STAGES_SPEC  = argGet(argStr, "stages");

print("=== mt_clip170 pipeline ===");
print("image       : " + IMAGE_PATH);
print("out_dir     : " + OUT_DIR);
print("params_path : " + PARAMS_PATH);
print("stages      : " + STAGES_SPEC);

// parse stages "A-B"
dashIdx = indexOf(STAGES_SPEC, "-");
if (dashIdx < 0) {
    STAGE_START = parseInt(STAGES_SPEC);
    STAGE_END   = STAGE_START;
} else {
    STAGE_START = parseInt(substring(STAGES_SPEC, 0, dashIdx));
    STAGE_END   = parseInt(substring(STAGES_SPEC, dashIdx + 1, lengthOf(STAGES_SPEC)));
}
print("stage range : " + STAGE_START + " .. " + STAGE_END);

// ensure out_dir exists (should already — driver creates it)
File.makeDirectory(OUT_DIR);

function shouldRun(n) {
    return (n >= STAGE_START && n <= STAGE_END);
}

function stageBanner(n, name) {
    print("");
    print("───────────── STAGE " + n + " · " + name + " ─────────────");
}

// ============================================================
// ── parameter loading (JSON-lite: read the JSON as text and
//    extract values using string operations — no JSON lib in
//    ImageJ macro language)
// ============================================================
// We read the JSON once into a string, then provide helpers
// paramNum / paramStr / paramBool that pull values by path.

paramsText = File.openAsString(PARAMS_PATH);
if (lengthOf(paramsText) == 0) exit("empty params file: " + PARAMS_PATH);

// ── JSON-lite accessors ──
// paramStr("dapi.threshold_method") returns the string value (stripped of quotes)
// paramNum(...) returns a number, paramBool(...) returns 0/1.
// Implementation: find the last key in the path, then the next : and
// the next , or } as the value end. Works because our param file is flat
// per-section with no nested objects within the leaf values.

// Keys in the JSON are dotted (written flat by driver.py):
//   "channels.tubulin": 1, "dapi.threshold_method": "Otsu", ...
// We look up by full dotted path — collision-free.
function _jsonFind(path) {
    needle = "\"" + path + "\":";
    i = indexOf(paramsText, needle);
    if (i < 0) return "";
    i = i + lengthOf(needle);
    // skip whitespace
    while (i < lengthOf(paramsText) && (substring(paramsText, i, i+1) == " " || substring(paramsText, i, i+1) == "\t" || substring(paramsText, i, i+1) == "\n")) i++;
    // find end (comma or newline or closing brace)
    j = i;
    inStr = false;
    while (j < lengthOf(paramsText)) {
        ch = substring(paramsText, j, j+1);
        if (ch == "\"") inStr = !inStr;
        if (!inStr) {
            if (ch == "," || ch == "}" || ch == "\n") break;
        }
        j++;
    }
    val = substring(paramsText, i, j);
    while (lengthOf(val) > 0 && (startsWith(val, " ") || startsWith(val, "\t"))) val = substring(val, 1, lengthOf(val));
    while (lengthOf(val) > 0 && (endsWith(val, " ") || endsWith(val, "\t") || endsWith(val, "\r"))) val = substring(val, 0, lengthOf(val) - 1);
    return val;
}

function paramStr(path) {
    v = _jsonFind(path);
    if (lengthOf(v) >= 2 && startsWith(v, "\"") && endsWith(v, "\"")) {
        v = substring(v, 1, lengthOf(v) - 1);
    }
    return v;
}

function paramNum(path) {
    v = _jsonFind(path);
    if (v == "" || v == "null") return NaN;
    return parseFloat(v);
}

function paramBool(path) {
    v = _jsonFind(path);
    if (v == "true") return 1;
    if (v == "1") return 1;
    return 0;
}

// ============================================================
// ── STAGE 0 · load + split + calibration ────────────────────
// ============================================================
PX_UM = 0;  // pixel size in microns, set in Stage 0
CH_TUBULIN = 1; CH_GFP = 2; CH_DAPI = 3;

if (shouldRun(0)) {
    stageBanner(0, "load + split + calibration");
    t0 = getTime();

    // Bio-Formats Macro Extensions — avoids the VerifyError from the plugin dialog path
    run("Bio-Formats Macro Extensions");
    Ext.openImagePlus(IMAGE_PATH);
    srcTitle = getTitle();
    getDimensions(W, H, C, S, F);
    getPixelSize(unit, pw, ph);
    print("image        : " + srcTitle);
    print("dimensions   : " + W + "x" + H + " C=" + C + " Z=" + S + " T=" + F);
    print("bitDepth     : " + bitDepth());
    print("pixelSize    : " + pw + " " + unit);
    PX_UM = pw;

    if (C < 3) exit("expected >= 3 channels, got " + C);

    CH_TUBULIN = paramNum("channels.tubulin"); if (isNaN(CH_TUBULIN)) CH_TUBULIN = 1;
    CH_GFP     = paramNum("channels.gfp");     if (isNaN(CH_GFP))     CH_GFP     = 2;
    CH_DAPI    = paramNum("channels.dapi");    if (isNaN(CH_DAPI))    CH_DAPI    = 3;

    // Split channels
    run("Split Channels");
    // After Split Channels, the windows are named "C1-<srcTitle>", etc.
    // Rename to standard names.
    selectWindow("C" + CH_TUBULIN + "-" + srcTitle); rename("ch_tubulin"); saveAs("Tiff", OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
    selectWindow("C" + CH_GFP + "-" + srcTitle);     rename("ch_gfp");     saveAs("Tiff", OUT_DIR + "/ch_gfp.tif");     rename("ch_gfp");
    selectWindow("C" + CH_DAPI + "-" + srcTitle);    rename("ch_dapi");    saveAs("Tiff", OUT_DIR + "/ch_dapi.tif");    rename("ch_dapi");

    // Write calibration.json (we write it manually; simple fields)
    calibPath = OUT_DIR + "/calibration.json";
    calibStr = "{\n";
    calibStr = calibStr + "  \"image\": \"" + IMAGE_PATH + "\",\n";
    calibStr = calibStr + "  \"width\": " + W + ",\n";
    calibStr = calibStr + "  \"height\": " + H + ",\n";
    calibStr = calibStr + "  \"channels\": " + C + ",\n";
    calibStr = calibStr + "  \"bit_depth\": " + bitDepth() + ",\n";
    calibStr = calibStr + "  \"pixel_size_um\": " + pw + ",\n";
    calibStr = calibStr + "  \"unit\": \"" + unit + "\"\n";
    calibStr = calibStr + "}\n";
    File.saveString(calibStr, calibPath);
    print("wrote        : " + calibPath);

    // QC: 3-panel PNG with identical display (percentile stretch) on each channel
    // We make an 8-bit copy per channel for display only.
    for (ci = 0; ci < 3; ci++) {
        if      (ci == 0) name = "ch_tubulin";
        else if (ci == 1) name = "ch_gfp";
        else              name = "ch_dapi";
        selectWindow(name);
        run("Duplicate...", "title=" + name + "_disp");
        // stretch display to 0.1%..99.9%
        getStatistics(area, mean, mn, mx);
        // Use a robust percentile via histogram
        run("Enhance Contrast", "saturated=0.35");
        run("8-bit");
        run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
        run("Flatten");
        saveAs("PNG", OUT_DIR + "/_00_" + name + ".png");
        close(); // flattened
        close(); // _disp
    }

    // Combine the 3 PNGs into one row via Image > Stacks > Images to Stack > Make Montage
    open(OUT_DIR + "/_00_ch_tubulin.png"); rename("p1");
    open(OUT_DIR + "/_00_ch_gfp.png");     rename("p2");
    open(OUT_DIR + "/_00_ch_dapi.png");    rename("p3");
    run("Images to Stack", "name=stack title=p use");
    run("Make Montage...", "columns=3 rows=1 scale=1");
    saveAs("PNG", OUT_DIR + "/00_raw_channels.png");
    close("*");

    // Reopen the full-precision split channel TIFFs so downstream stages can use them
    open(OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
    open(OUT_DIR + "/ch_gfp.tif");     rename("ch_gfp");
    open(OUT_DIR + "/ch_dapi.tif");    rename("ch_dapi");

    print("stage 0 done : " + ((getTime() - t0) / 1000) + "s");
} else {
    // Starting from a later stage: load previously-saved split channels from OUT_DIR.
    if (File.exists(OUT_DIR + "/ch_tubulin.tif")) {
        open(OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
        open(OUT_DIR + "/ch_gfp.tif");     rename("ch_gfp");
        open(OUT_DIR + "/ch_dapi.tif");    rename("ch_dapi");
        selectWindow("ch_tubulin");
        getPixelSize(unit, pw, ph);
        PX_UM = pw;
        print("reloaded split channels from " + OUT_DIR + " (px=" + PX_UM + " um)");
    }
}

// ============================================================
// ── STAGES 1..8 · stubs (to be implemented one by one) ─────
// ============================================================
// ============================================================
// ── STAGE 1 · nuclei seeds (DAPI) ───────────────────────────
// ============================================================
if (shouldRun(1)) {
    stageBanner(1, "nuclei seeds (DAPI)");
    t1 = getTime();

    if (!isOpen("ch_dapi")) {
        if (File.exists(OUT_DIR + "/ch_dapi.tif")) {
            open(OUT_DIR + "/ch_dapi.tif");
            rename("ch_dapi");
        } else {
            print("FATAL: Stage 1 needs ch_dapi — run Stage 0 first or pass --reuse <dir>");
            exit("missing ch_dapi");
        }
    }

    selectWindow("ch_dapi");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;
    print("pixel size   : " + pw + " um/px");

    sigma_um = paramNum("dapi.gaussian_sigma_um");
    th_method = paramStr("dapi.threshold_method");
    min_area_um2 = paramNum("dapi.min_nucleus_area_um2");
    do_fill = paramBool("dapi.fill_holes");
    do_ws = paramBool("dapi.watershed_split");

    sigma_px = sigma_um / pw;
    min_area_px = min_area_um2 / (pw * pw);
    print("gaussian σ   : " + sigma_um + " um (" + sigma_px + " px)");
    print("threshold    : " + th_method);
    print("min area     : " + min_area_um2 + " um² (" + min_area_px + " px)");
    print("fill holes   : " + do_fill);
    print("watershed    : " + do_ws);

    // Work on a duplicate — never touch raw ch_dapi
    run("Duplicate...", "title=dapi_work");
    run("Gaussian Blur...", "sigma=" + sigma_px);
    setAutoThreshold(th_method + " dark");
    setOption("BlackBackground", true);
    run("Convert to Mask");
    if (do_fill) run("Fill Holes");
    if (do_ws)   run("Watershed");

    // Headless-safe: build a label image via connected-component analysis.
    // MorphoLibJ's `Connected Components Labeling` works without a display and
    // produces a per-object label image with unique integer IDs.
    run("Connected Components Labeling", "connectivity=8 type=[16 bits]");
    rename("cc_raw");

    // Filter by size and border contact. We use Label Size Opening (MorphoLibJ)
    // to drop small labels, then Remove Border Labels.
    min_area_px_int = round(min_area_px);
    run("Label Size Opening", "min=" + min_area_px_int);
    run("Remove Border Labels", "left right top bottom");
    rename("nuclei_labels_raw");

    // Remap labels to contiguous 1..N for downstream predictability
    run("Remap Labels");
    rename("nuclei_labels");

    // Count distinct non-zero labels by sampling max pixel value
    getStatistics(area_total, mean_total, min_total, max_total);
    n_nuclei = max_total;
    print("nuclei found : " + n_nuclei);
    if (n_nuclei == 0) {
        print("WARNING: no nuclei passed filters — check threshold_method / min_nucleus_area_um2");
    }

    // Set calibration on the label image so downstream stages have it
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/nuclei_labels.tif");
    rename("nuclei_labels");

    // ── QC: build a binary edge mask from the label image ──
    selectWindow("nuclei_labels");
    run("Duplicate...", "title=nuclei_edges_16");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");  // -> 8-bit, 0/255
    run("Dilate");            // 2-px outline for visibility
    rename("nuclei_edges");

    // ── QC 1: DAPI (8-bit stretched) with white edges overlaid ──
    selectWindow("ch_dapi");
    run("Duplicate...", "title=qc_dapi");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    imageCalculator("Max", "qc_dapi", "nuclei_edges");  // burn edges into grayscale
    selectWindow("qc_dapi");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/01_nuclei_labels.png");
    print("wrote        : 01_nuclei_labels.png");

    close("qc_dapi*");
    close("nuclei_edges*");
    close("dapi_work*");
    close("cc_raw*");
    close("nuclei_labels_raw*");

    print("stage 1 done : " + ((getTime() - t1) / 1000) + "s");
}
// ============================================================
// ── STAGE 2 · tubulin whole-cell mask ───────────────────────
// ============================================================
if (shouldRun(2)) {
    stageBanner(2, "tubulin cell mask");
    t2 = getTime();

    if (!isOpen("ch_tubulin")) {
        if (File.exists(OUT_DIR + "/ch_tubulin.tif")) {
            open(OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
        } else {
            print("FATAL: Stage 2 needs ch_tubulin — run Stage 0 first or pass --reuse <dir>");
            exit("missing ch_tubulin");
        }
    }

    selectWindow("ch_tubulin");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    bg_um     = paramNum("tubulin_mask.bg_rolling_ball_um");
    sigma_um2 = paramNum("tubulin_mask.gaussian_sigma_um");
    th2       = paramStr("tubulin_mask.threshold_method");
    do_fill2  = paramBool("tubulin_mask.fill_holes");

    bg_px     = bg_um / pw;
    sigma_px2 = sigma_um2 / pw;
    print("rolling ball : " + bg_um + " um (" + bg_px + " px)");
    print("gaussian σ   : " + sigma_um2 + " um (" + sigma_px2 + " px)");
    print("threshold    : " + th2);
    print("fill holes   : " + do_fill2);

    run("Duplicate...", "title=tub_work");
    run("Subtract Background...", "rolling=" + bg_px);
    run("Gaussian Blur...", "sigma=" + sigma_px2);
    setAutoThreshold(th2 + " dark");
    setOption("BlackBackground", true);
    run("Convert to Mask");
    if (do_fill2) run("Fill Holes");

    // Report coverage for sanity
    getStatistics(area_tot, mean_m);
    coverage = mean_m / 255;  // fraction of white pixels (mean of a 0/255 mask / 255)
    print("mask coverage: " + (coverage * 100) + " %");

    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/cell_mask.tif");
    rename("cell_mask");

    // ── QC: tubulin (8-bit stretched) with mask outline overlaid ──
    // Build an edge map from the mask (Find Edges on 8-bit mask)
    selectWindow("cell_mask");
    run("Duplicate...", "title=mask_edges");
    run("Find Edges");
    setThreshold(1, 255);
    run("Convert to Mask");
    run("Dilate");
    rename("mask_edges");

    selectWindow("ch_tubulin");
    run("Duplicate...", "title=qc_tub");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    imageCalculator("Max", "qc_tub", "mask_edges");
    selectWindow("qc_tub");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/02_tubulin_mask.png");
    print("wrote        : 02_tubulin_mask.png");

    close("qc_tub*");
    close("mask_edges*");
    close("tub_work*");

    print("stage 2 done : " + ((getTime() - t2) / 1000) + "s");
}
// ============================================================
// ── STAGE 3 · marker-controlled watershed cell isolation ────
// ============================================================
if (shouldRun(3)) {
    stageBanner(3, "marker-controlled watershed");
    t3 = getTime();

    // Reload inputs if missing
    if (!isOpen("ch_tubulin")) {
        open(OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
    }
    if (!isOpen("nuclei_labels")) {
        if (!File.exists(OUT_DIR + "/nuclei_labels.tif")) {
            print("FATAL: Stage 3 needs nuclei_labels — run Stage 1 first");
            exit("missing nuclei_labels");
        }
        open(OUT_DIR + "/nuclei_labels.tif"); rename("nuclei_labels");
    }
    if (!isOpen("cell_mask")) {
        if (!File.exists(OUT_DIR + "/cell_mask.tif")) {
            print("FATAL: Stage 3 needs cell_mask — run Stage 2 first");
            exit("missing cell_mask");
        }
        open(OUT_DIR + "/cell_mask.tif"); rename("cell_mask");
    }

    selectWindow("ch_tubulin");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    ws_input       = paramStr("cells.watershed_input");  // "intensity_inverted" | "distance_map"
    min_cell_um2   = paramNum("cells.min_area_um2");
    max_cell_um2   = paramNum("cells.max_area_um2");
    exclude_border = paramBool("cells.exclude_border");

    min_cell_px = min_cell_um2 / (pw * pw);
    max_cell_px = max_cell_um2 / (pw * pw);
    print("ws input     : " + ws_input);
    print("min area     : " + min_cell_um2 + " um² (" + min_cell_px + " px)");
    print("max area     : " + max_cell_um2 + " um² (" + max_cell_px + " px)");
    print("exclude bord : " + exclude_border);

    // ── Build the watershed input image ──
    if (ws_input == "distance_map") {
        // Distance transform inside the cell mask, inverted so basins = cell centers
        selectWindow("cell_mask");
        run("Duplicate...", "title=ws_input");
        run("Distance Map");           // -> 32-bit distance map
        run("Invert");
    } else {
        // Default: use the tubulin intensity, inverted so bright regions are basins
        selectWindow("ch_tubulin");
        run("Duplicate...", "title=ws_input");
        run("Enhance Contrast", "saturated=0.35");
        run("8-bit");
        run("Invert");
    }

    // ── Run MorphoLibJ Marker-controlled Watershed ──
    // Command signature (MorphoLibJ 1.6):
    //   Marker-controlled Watershed: input=... marker=... mask=... calculate use
    run("Marker-controlled Watershed",
        "input=ws_input marker=nuclei_labels mask=cell_mask calculate use");
    // Result window is named "<input>-watershed"
    rename("cells_raw");

    // ── Filter by size and (optionally) border contact ──
    if (exclude_border) {
        run("Remove Border Labels", "left right top bottom");
    }
    run("Label Size Opening", "min=" + round(min_cell_px));
    // Upper bound: set any label whose area > max to 0 using `Label Size Filtering`
    run("Label Size Filtering", "operation=Lower_Than_Or_Equal size=" + round(max_cell_px));

    // Remap to contiguous 1..N so downstream stages use predictable IDs
    run("Remap Labels");
    rename("cell_labels");

    // Count
    getStatistics(a_t, m_t, mn_t, mx_t);
    n_cells = mx_t;
    print("cells found  : " + n_cells);
    if (n_cells == 0) {
        print("WARNING: no cells survived filtering — adjust cells.min_area_um2 or watershed_input");
    }

    // Save calibrated label image
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/cell_labels.tif");
    rename("cell_labels");

    // ── Per-cell area table (cells_area.csv) ──
    // Walk labels 1..N and measure with getRawStatistics so pixel counts are
    // always raw regardless of image calibration.
    csv = "cell_id,area_px,area_um2,centroid_x_px,centroid_y_px\n";
    for (lid = 1; lid <= n_cells; lid++) {
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        if (selectionType() >= 0) {
            getRawStatistics(nPx, mean_v);
            getSelectionBounds(bx, by, bw, bh);
            cx = bx + bw / 2;
            cy = by + bh / 2;
            area_um2 = nPx * pw * pw;
            csv = csv + lid + "," + nPx + "," + area_um2 + "," + cx + "," + cy + "\n";
        }
        run("Select None");
        resetThreshold();
    }
    File.saveString(csv, OUT_DIR + "/cells_area.csv");
    print("wrote        : cells_area.csv");

    // ── QC overlay: tubulin 8-bit + yellow cell outlines + numeric labels ──
    // 1. Edge mask of the cell labels
    selectWindow("cell_labels");
    run("Duplicate...", "title=cell_edges_16");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");
    run("Dilate");
    rename("cell_edges");

    // 2. 8-bit stretched tubulin with edges burned in
    selectWindow("ch_tubulin");
    run("Duplicate...", "title=qc_cells");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    imageCalculator("Max", "qc_cells", "cell_edges");

    // 3. Draw numeric labels at each cell centroid
    selectWindow("qc_cells");
    setFont("SansSerif", 28, "bold");
    setColor(255);
    // Centroids from cells_area.csv entries — re-derive from label map
    for (lid = 1; lid <= n_cells; lid++) {
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        if (selectionType() >= 0) {
            getSelectionBounds(bx, by, bw, bh);
            cx = bx + bw / 2;
            cy = by + bh / 2;
            selectWindow("qc_cells");
            drawString("" + lid, cx - 10, cy + 10);
            selectWindow("cell_labels");
        }
        run("Select None");
        resetThreshold();
    }

    selectWindow("qc_cells");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/03_cell_labels.png");
    print("wrote        : 03_cell_labels.png");

    close("qc_cells*");
    close("cell_edges*");
    close("cell_edges_16*");
    close("cells_raw*");
    close("ws_input*");

    print("stage 3 done : " + ((getTime() - t3) / 1000) + "s");
}
// ============================================================
// ── STAGE 4 · GFP+ classification ───────────────────────────
// ============================================================
if (shouldRun(4)) {
    stageBanner(4, "GFP+ classification");
    t4 = getTime();

    if (!isOpen("ch_gfp")) {
        if (File.exists(OUT_DIR + "/ch_gfp.tif")) {
            open(OUT_DIR + "/ch_gfp.tif"); rename("ch_gfp");
        } else {
            print("FATAL: Stage 4 needs ch_gfp — run Stage 0 first");
            exit("missing ch_gfp");
        }
    }
    if (!isOpen("cell_labels")) {
        if (!File.exists(OUT_DIR + "/cell_labels.tif")) {
            print("FATAL: Stage 4 needs cell_labels — run Stage 3 first");
            exit("missing cell_labels");
        }
        open(OUT_DIR + "/cell_labels.tif"); rename("cell_labels");
    }

    selectWindow("ch_gfp");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    fold      = paramNum("gfp_classification.fold_over_bg");     // may be NaN
    abs_delta = paramNum("gfp_classification.absolute_delta");   // may be NaN
    mad_mult  = paramNum("gfp_classification.mad_multiplier");   // may be NaN
    abs_gate  = paramNum("gfp_classification.absolute_gate_value"); // may be NaN
    print("mad mult     : " + mad_mult);
    print("abs gate val : " + abs_gate);
    print("fold over bg : " + fold);
    if (!isNaN(abs_delta)) print("absolute delta: " + abs_delta + " gray values");

    // ── Step 1: background estimate from pixels OUTSIDE all cell ROIs ──
    // Build a "non-cell" mask = (cell_labels == 0), then measure ch_gfp there.
    selectWindow("cell_labels");
    run("Duplicate...", "title=noncell_mask_16");
    setThreshold(0, 0);
    setOption("BlackBackground", true);
    run("Convert to Mask");    // -> 8-bit; pixels outside any cell become 255
    rename("noncell_mask");

    // Create a selection from the non-cell mask, apply it to ch_gfp, measure.
    selectWindow("noncell_mask");
    run("Create Selection");
    selectWindow("ch_gfp");
    run("Restore Selection");
    getRawStatistics(bg_nPx, bg_mean, bg_min, bg_max, bg_stddev);

    // Robust center (median) via Analyze > Measure.
    run("Set Measurements...", "area mean median redirect=None decimal=3");
    run("Clear Results");
    run("Measure");
    bg_median = getResult("Median", 0);
    run("Clear Results");

    // ── Robust spread: MAD = median(|x - median(x)|) ──
    // Duplicate ch_gfp as 32-bit, subtract bg_median, take abs, re-select non-cell ROI, measure median.
    selectWindow("ch_gfp");
    run("Select None");
    run("Duplicate...", "title=bg_dev");
    run("32-bit");
    run("Subtract...", "value=" + bg_median);
    run("Abs");
    // Re-apply the non-cell selection
    selectWindow("noncell_mask");
    run("Create Selection");
    selectWindow("bg_dev");
    run("Restore Selection");
    run("Clear Results");
    run("Measure");
    mad_raw = getResult("Median", 0);
    run("Clear Results");
    run("Select None");
    close("bg_dev");

    // Robust stddev ≈ 1.4826 × MAD  (for Gaussian-like distributions)
    robust_std = 1.4826 * mad_raw;

    selectWindow("ch_gfp");
    run("Select None");

    print("bg pixels    : " + bg_nPx);
    print("bg mean      : " + bg_mean);
    print("bg median    : " + bg_median);
    print("bg stddev    : " + bg_stddev + " (non-robust, inflated by outliers)");
    print("bg MAD       : " + mad_raw + " -> robust_std = " + robust_std);

    // Compute candidate gates and pick the most stringent (highest).
    gate_mad   = -1;
    gate_fold  = -1;
    gate_delta = -1;
    gate_absV  = -1;
    if (!isNaN(mad_mult) && mad_mult > 0) {
        gate_mad = bg_median + mad_mult * robust_std;
        print("  gate (MAD)   : " + gate_mad + "   [median + " + mad_mult + " * robust_std]");
    }
    if (!isNaN(fold) && fold > 0) {
        gate_fold = bg_median * fold;
        print("  gate (fold)  : " + gate_fold + "   [median * " + fold + "]");
    }
    if (!isNaN(abs_delta) && abs_delta > 0) {
        gate_delta = bg_median + abs_delta;
        print("  gate (delta) : " + gate_delta + "   [median + " + abs_delta + "]");
    }
    if (!isNaN(abs_gate) && abs_gate > 0) {
        gate_absV = abs_gate;
        print("  gate (abs)   : " + gate_absV + "   [fixed value]");
    }
    abs_thresh = gate_mad;
    if (gate_fold  > abs_thresh) abs_thresh = gate_fold;
    if (gate_delta > abs_thresh) abs_thresh = gate_delta;
    if (gate_absV  > abs_thresh) abs_thresh = gate_absV;
    if (abs_thresh < 0) {
        abs_thresh = bg_median * 2;
        print("  WARNING: no gate enabled; defaulting to bg_median*2 = " + abs_thresh);
    }
    print("GFP+ gate    : mean_gfp > " + abs_thresh);

    // ── Step 2: per-cell mean GFP ──
    // cell count
    selectWindow("cell_labels");
    getRawStatistics(area_cl, mean_cl, min_cl, max_cl_lbl);
    n_cells = max_cl_lbl;
    print("cells to gate: " + n_cells);

    csv = "cell_id,mean_gfp,is_gfp_positive,bg_median,bg_robust_std,gate\n";
    n_pos = 0;
    positives = "";  // comma-separated list of positive cell IDs for later stages
    for (lid = 1; lid <= n_cells; lid++) {
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            continue;
        }
        // Transfer selection to ch_gfp and measure
        selectWindow("ch_gfp");
        run("Restore Selection");
        getRawStatistics(nPx_c, mean_c);
        if (mean_c > abs_thresh) is_pos = 1; else is_pos = 0;
        if (is_pos == 1) {
            n_pos = n_pos + 1;
            if (lengthOf(positives) > 0) positives = positives + ",";
            positives = positives + lid;
        }
        csv = csv + lid + "," + mean_c + "," + is_pos + "," + bg_median + "," + robust_std + "," + abs_thresh + "\n";
        run("Select None");
    }
    File.saveString(csv, OUT_DIR + "/gfp_classification.csv");
    print("GFP+ cells   : " + n_pos + " / " + n_cells);

    // Also save the positive ID list as a plain text file for downstream reads
    File.saveString(positives, OUT_DIR + "/gfp_positive_ids.txt");

    // ── QC overlay: GFP channel (8-bit stretched) with cell outlines ──
    // All outlines are white; label text is green (pos) or red (neg), including mean value.
    selectWindow("cell_labels");
    run("Duplicate...", "title=all_edges_16");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");
    run("Dilate");
    rename("all_edges");

    selectWindow("ch_gfp");
    run("Duplicate...", "title=qc_gfp");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    imageCalculator("Max", "qc_gfp", "all_edges");

    selectWindow("qc_gfp");
    run("RGB Color");

    setFont("SansSerif", 26, "bold");
    for (lid = 1; lid <= n_cells; lid++) {
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        resetThreshold();
        if (selectionType() >= 0) {
            getSelectionBounds(bx, by, bw, bh);
            cx = bx + bw / 2;
            cy = by + bh / 2;
            selectWindow("ch_gfp");
            run("Restore Selection");
            getRawStatistics(nPx_c, mean_c);
            run("Select None");
            selectWindow("qc_gfp");
            if (mean_c > abs_thresh) setColor(120, 255, 120);
            else                     setColor(255, 120, 120);
            lbl = lid + ":" + round(mean_c);
            drawString(lbl, cx - 30, cy + 10);
        }
    }
    // Gate info in the corner
    setColor(255, 255, 0);
    drawString("bg=" + round(bg_median) + "  MAD*1.48=" + round(robust_std) + "  gate=" + round(abs_thresh) + "  k=" + mad_mult, 20, 40);

    selectWindow("qc_gfp");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/04_gfp_classification.png");
    print("wrote        : 04_gfp_classification.png");

    close("qc_gfp*");
    close("all_edges*");
    close("all_edges_16*");
    close("noncell_mask*");

    print("stage 4 done : " + ((getTime() - t4) / 1000) + "s");
}
// ============================================================
// ── STAGE 5 · microtubule length (Tubeness + Skeleton) ─────
// ============================================================
if (shouldRun(5)) {
    stageBanner(5, "MT length (Tubeness + Skeleton)");
    t5 = getTime();

    // Reload inputs if needed
    if (!isOpen("ch_tubulin")) {
        open(OUT_DIR + "/ch_tubulin.tif"); rename("ch_tubulin");
    }
    if (!isOpen("cell_labels")) {
        if (!File.exists(OUT_DIR + "/cell_labels.tif")) {
            print("FATAL: Stage 5 needs cell_labels — run Stage 3 first");
            exit("missing cell_labels");
        }
        open(OUT_DIR + "/cell_labels.tif"); rename("cell_labels");
    }
    if (!File.exists(OUT_DIR + "/gfp_positive_ids.txt")) {
        print("FATAL: Stage 5 needs gfp_positive_ids.txt — run Stage 4 first");
        exit("missing gfp_positive_ids.txt");
    }
    positives = File.openAsString(OUT_DIR + "/gfp_positive_ids.txt");
    // strip trailing whitespace/newlines
    while (lengthOf(positives) > 0 && (endsWith(positives, "\n") || endsWith(positives, "\r") || endsWith(positives, " "))) {
        positives = substring(positives, 0, lengthOf(positives) - 1);
    }
    if (lengthOf(positives) == 0) {
        print("No GFP+ cells — skipping Stage 5");
    } else {

    print("GFP+ ids     : " + positives);

    selectWindow("ch_tubulin");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    // ── Ridge Detection parameters ──
    rd_width_um   = paramNum("mt.ridge_line_width_um");
    rd_sigma      = paramNum("mt.ridge_sigma");
    rd_high       = paramNum("mt.ridge_high_contrast");
    rd_low        = paramNum("mt.ridge_low_contrast");
    rd_lowerT     = paramNum("mt.ridge_lower_threshold");
    rd_upperT     = paramNum("mt.ridge_upper_threshold");
    rd_minlen_um  = paramNum("mt.ridge_min_line_length_um");

    rd_width_px   = rd_width_um / pw;
    rd_minlen_px  = rd_minlen_um / pw;

    // ── Complementary coverage metric (Tubeness + Triangle) ──
    cov_sigma_um  = paramNum("mt.coverage_tubeness_sigma_um");
    cov_th        = paramStr("mt.coverage_threshold_method");
    cov_sigma_px  = cov_sigma_um / pw;

    print("RIDGE DETECTION params:");
    print("  line_width   : " + rd_width_um + " um (" + rd_width_px + " px)");
    print("  sigma        : " + rd_sigma);
    print("  contrast hi/lo: " + rd_high + " / " + rd_low + " (8-bit)");
    print("  gradT lo/hi  : " + rd_lowerT + " / " + rd_upperT);
    print("  min length   : " + rd_minlen_um + " um (" + rd_minlen_px + " px)");
    print("COVERAGE (Tubeness+" + cov_th + "):");
    print("  tubeness σ   : " + cov_sigma_um + " um (" + cov_sigma_px + " px)");

    // ── Step 1: Prepare 8-bit tubulin (global) ──────────────────
    // NOTE: Ridge Detection on the full 2048² image is super-linear in time
    // for dense filament networks (>5 min). We process each GFP+ cell on its
    // own bounding-box crop and paste ridges back into a global mt_ridges image.
    selectWindow("ch_tubulin");
    run("Duplicate...", "title=tub_for_ridges");
    getRawStatistics(raw_n, raw_mean, raw_min, raw_max);
    print("  raw tubulin min/max/mean  : " + raw_min + " / " + raw_max + " / " + raw_mean);
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    rename("tub_8bit");

    // Save 8-bit version for inspection
    run("Duplicate...", "title=tub_8bit_save");
    saveAs("PNG", OUT_DIR + "/_05_tubulin_8bit.png");
    close();
    selectWindow("tub_8bit");

    // Empty global ridges accumulator (same size as ch_tubulin, 8-bit, all zeros)
    newImage("mt_ridges", "8-bit black", getWidth(), getHeight(), 1);
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);

    // Per-cell Ridge Detection
    t_ridge_all = getTime();
    selectWindow("cell_labels");
    getRawStatistics(_dum, _dum2, _dum3, n_cells_probe);
    n_cells_probe = round(n_cells_probe);
    pos_padded_s5 = "," + positives + ",";
    margin_px = 10;   // crop margin around each cell bbox

    for (cid = 1; cid <= n_cells_probe; cid++) {
        if (indexOf(pos_padded_s5, "," + cid + ",") < 0) continue;  // skip GFP−

        t_cell = getTime();
        // Get cell bbox from cell_labels
        selectWindow("cell_labels");
        setThreshold(cid, cid);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            print("  cell " + cid + ": empty selection, skipping");
            continue;
        }
        getSelectionBounds(bbx, bby, bbw, bbh);
        // Expand by margin (clipped to image)
        x0 = bbx - margin_px; if (x0 < 0) x0 = 0;
        y0 = bby - margin_px; if (y0 < 0) y0 = 0;
        x1 = bbx + bbw + margin_px; if (x1 > getWidth()) x1 = getWidth();
        y1 = bby + bbh + margin_px; if (y1 > getHeight()) y1 = getHeight();
        cw = x1 - x0;
        ch2 = y1 - y0;

        // Crop the 8-bit tubulin to this bbox
        selectWindow("tub_8bit");
        makeRectangle(x0, y0, cw, ch2);
        run("Duplicate...", "title=crop_tub");
        run("Select None");

        // Mask crop_tub to THIS cell only: build mask from label crop
        selectWindow("cell_labels");
        makeRectangle(x0, y0, cw, ch2);
        run("Duplicate...", "title=crop_lbl");
        run("Select None");
        // Restrict to lid == cid
        setThreshold(cid, cid);
        run("Convert to Mask");   // 8-bit 0/255 where pixel==cid
        rename("crop_cell_mask");

        // Zero out tubulin outside cell mask: tub = tub * (mask/255)
        // Use imageCalculator AND with mask
        imageCalculator("Min create", "crop_tub", "crop_cell_mask");
        rename("crop_tub_masked");
        close("crop_tub");
        close("crop_cell_mask");

        // Run Ridge Detection on this small crop
        selectWindow("crop_tub_masked");
        run("Ridge Detection",
            "line_width=" + rd_width_px +
            " high_contrast=" + rd_high +
            " low_contrast=" + rd_low +
            " lower_threshold=" + rd_lowerT +
            " upper_threshold=" + rd_upperT +
            " minimum_line_length=" + rd_minlen_px +
            " sigma=" + rd_sigma +
            " make_binary" +
            " method_for_overlap_resolution=SLOPE");

        // Find the binary result ("<input title> Detected segments")
        segs_title = "";
        list = getList("image.titles");
        for (lix = 0; lix < list.length; lix++) {
            if (indexOf(list[lix], "Detected segments") >= 0) {
                segs_title = list[lix];
                lix = list.length;
            }
        }
        if (segs_title == "") {
            print("  cell " + cid + ": Ridge Detection produced no output, skipping");
            close("crop_tub_masked*");
            continue;
        }

        // MAX-merge the binary crop into mt_ridges at (x0, y0). `Paste` would
        // overwrite any previous content from overlapping neighbor crops, so
        // we duplicate the existing crop region, Max with the new ridges,
        // then paste the combined result back.
        selectWindow("mt_ridges");
        makeRectangle(x0, y0, cw, ch2);
        run("Duplicate...", "title=existing_crop");
        run("Select None");
        imageCalculator("Max", "existing_crop", segs_title);
        selectWindow("existing_crop");
        run("Select All");
        run("Copy");
        selectWindow("mt_ridges");
        makeRectangle(x0, y0, cw, ch2);
        run("Paste");
        run("Select None");
        close("existing_crop");

        // Cleanup per-cell windows
        close(segs_title);
        close("crop_tub_masked");

        // Per-cell runtime
        dtc = (getTime() - t_cell) / 1000;
        print("  cell " + cid + ": bbox " + cw + "x" + ch2 + " in " + dtc + "s");
    }
    print("  per-cell Ridge Detection total : " + ((getTime() - t_ridge_all) / 1000) + " s");

    // mt_ridges is the accumulated global result
    selectWindow("mt_ridges");
    getRawStatistics(r_n, r_mean);
    n_ridge_fg = round(r_mean * r_n / 255);
    ridge_cov = 100 * n_ridge_fg / r_n;
    print("  ridge fg px  : " + n_ridge_fg + "  (" + ridge_cov + " % of image)");
    print("  total length : " + (n_ridge_fg * pw) + " um");

    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/mt_ridges.tif");
    rename("mt_ridges");

    close("tub_8bit");
    close("tub_for_ridges");

    // ── Step 2: Coverage mask (Tubeness + threshold) ──
    selectWindow("ch_tubulin");
    run("Duplicate...", "title=tub_raw");
    run("Tubeness", "sigma=" + cov_sigma_px + " use");
    rename("tub_tubeness");

    // Save the Tubeness response as a diagnostic PNG
    run("Duplicate...", "title=tub_tubeness_save");
    run("Enhance Contrast", "saturated=0.1");
    run("8-bit");
    saveAs("PNG", OUT_DIR + "/_05_tubeness.png");
    close();
    selectWindow("tub_tubeness");

    setAutoThreshold(cov_th + " dark");
    setOption("BlackBackground", true);
    run("Convert to Mask");
    rename("tub_mt_mask");
    getRawStatistics(mk_n, mk_mean);
    mk_cov = 100 * mk_mean / 255;
    print("  mt_mask cov  : " + mk_cov + " % (" + round(mk_mean * mk_n / 255) + " fg px)");
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/mt_mask.tif");
    rename("tub_mt_mask");

    // Rename mt_ridges -> mt_skeleton alias so the rest of the code (QC labels,
    // length accounting) can stay the same. We also keep a real mt_ridges
    // window for the composite.
    selectWindow("mt_ridges");
    run("Duplicate...", "title=mt_skeleton");

    // ── Step 4: Per-cell MT metrics ──
    //   mt_ridge_length_um = sum of Ridge Detection fg pixels inside cell × pw  (PRIMARY)
    //   mt_mask_area_um2   = coverage area from Tubeness+Triangle mask
    //   mt_density         = mt_mask_area / cell_area  (0..1)
    //   mt_intensity_int   = integrated tubulin intensity inside cell
    csv = "cell_id,is_gfp_positive,cell_area_um2,mt_ridge_length_um,mt_mask_area_um2,mt_density,mt_intensity_integrated\n";

    pos_padded = "," + positives + ",";
    selectWindow("cell_labels");
    getRawStatistics(at, mt_m, mt_mn, mt_mx);
    n_cells_total = mt_mx;

    for (lid = 1; lid <= n_cells_total; lid++) {
        is_pos_lid = 0;
        if (indexOf(pos_padded, "," + lid + ",") >= 0) is_pos_lid = 1;

        if (is_pos_lid != 1) {
            csv = csv + lid + "," + is_pos_lid + ",,,,,\n";
            continue;
        }

        // Cell area
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            csv = csv + lid + "," + is_pos_lid + ",0,0,0,0,0\n";
            continue;
        }
        getRawStatistics(cell_nPx);
        cell_area_um2 = cell_nPx * pw * pw;

        // MT mask area inside cell
        selectWindow("tub_mt_mask");
        run("Restore Selection");
        getRawStatistics(mask_nPx, mask_mean);
        mask_fg_px = round(mask_mean * mask_nPx / 255);
        mask_area_um2 = mask_fg_px * pw * pw;
        density = 0;
        if (cell_nPx > 0) density = mask_fg_px / cell_nPx;
        run("Select None");

        // Skeleton length inside cell
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        resetThreshold();
        selectWindow("mt_skeleton");
        run("Restore Selection");
        getRawStatistics(skel_nPx, skel_mean);
        skel_fg_px = round(skel_mean * skel_nPx / 255);
        skel_length_um = skel_fg_px * pw;
        run("Select None");

        // Integrated tubulin intensity inside cell
        selectWindow("cell_labels");
        setThreshold(lid, lid);
        run("Create Selection");
        resetThreshold();
        selectWindow("ch_tubulin");
        run("Restore Selection");
        getRawStatistics(tub_nPx, tub_mean_c);
        tub_integrated = tub_mean_c * tub_nPx;
        run("Select None");

        csv = csv + lid + "," + is_pos_lid + "," + cell_area_um2 + ","
              + skel_length_um + "," + mask_area_um2 + "," + density + ","
              + tub_integrated + "\n";
    }
    File.saveString(csv, OUT_DIR + "/mt_lengths.csv");
    print("wrote        : mt_lengths.csv");

    // ── QC overlay: RGB composite ──
    //   Base  = DIM tubulin (~25% brightness) in all 3 channels → looks gray
    //   Green = MT skeleton (full brightness, 2 px fat) ON TOP of base
    //   Yellow = cell outlines (R+G additive) ON TOP of base
    selectWindow("cell_labels");
    run("Duplicate...", "title=cell_outline_16");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");
    run("Dilate");
    rename("cell_outline");
    run("Multiply...", "value=0.6");        // dim outline to ~150
    rename("cell_outline_dim");

    // Fat skeleton so it's visible at normal zoom
    selectWindow("mt_skeleton");
    run("Duplicate...", "title=mt_skel_fat");
    run("Dilate");
    run("Dilate");

    // Dim tubulin base — same image goes into all 3 channels
    selectWindow("ch_tubulin");
    run("Duplicate...", "title=tub_base");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    run("Multiply...", "value=0.25");        // background goes to ~25% max → clearly gray

    // R channel = base + yellow outlines
    selectWindow("tub_base");
    run("Duplicate...", "title=qc_R");
    imageCalculator("Max", "qc_R", "cell_outline_dim");

    // G channel = base + bright skeleton + yellow outlines
    selectWindow("tub_base");
    run("Duplicate...", "title=qc_G");
    imageCalculator("Max", "qc_G", "mt_skel_fat");
    imageCalculator("Max", "qc_G", "cell_outline_dim");

    // B channel = base only
    selectWindow("tub_base");
    run("Duplicate...", "title=qc_B");

    run("Merge Channels...", "c1=qc_R c2=qc_G c3=qc_B create");
    run("RGB Color");
    rename("qc_mt");

    // Label each GFP+ cell with ridge length (µm)
    setFont("SansSerif", 28, "bold");
    setColor(255, 255, 0);
    for (lid = 1; lid <= n_cells_total; lid++) {
        if (indexOf(pos_padded, "," + lid + ",") >= 0) {
            selectWindow("cell_labels");
            setThreshold(lid, lid);
            run("Create Selection");
            resetThreshold();
            if (selectionType() >= 0) {
                getSelectionBounds(bx, by, bw, bh);
                cx = bx + bw / 2;
                cy = by + bh / 2;
                selectWindow("mt_ridges");
                run("Restore Selection");
                getRawStatistics(rnPx, rmean);
                rfg = round(rmean * rnPx / 255);
                rlen = rfg * pw;
                run("Select None");
                selectWindow("qc_mt");
                drawString(lid + ":" + round(rlen) + "um", cx - 40, cy + 10);
            }
        }
    }

    selectWindow("qc_mt");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/05_mt_skeleton.png");
    print("wrote        : 05_mt_skeleton.png");

    // Also save a plain skeleton-on-black view for precise inspection
    selectWindow("mt_skeleton");
    run("Duplicate...", "title=skel_view");
    run("8-bit");
    run("RGB Color");
    saveAs("PNG", OUT_DIR + "/05_mt_skeleton_only.png");
    print("wrote        : 05_mt_skeleton_only.png");

    close("qc_*");
    close("tub_base*");
    close("mt_skel_fat*");
    close("cell_outline*");
    close("tub_raw*");
    close("tub_tubeness*");
    close("skel_view*");

    } // end if positives non-empty
    print("stage 5 done : " + ((getTime() - t5) / 1000) + "s");
}
// ============================================================
// ── STAGE 6 · CLIP170 lattice (Ridge Detection per cell) ────
// ============================================================
// This CLIP170 variant decorates the MT lattice, so we trace it as filaments
// via Ridge Detection (same approach as Stage 5 for MTs), with CLIP170-specific
// (more sensitive) parameters because GFP signal is typically dimmer than tubulin.
if (shouldRun(6)) {
    stageBanner(6, "CLIP170 lattice (Ridge Detection)");
    t6 = getTime();

    if (!isOpen("ch_gfp")) {
        open(OUT_DIR + "/ch_gfp.tif"); rename("ch_gfp");
    }
    if (!isOpen("cell_labels")) {
        if (!File.exists(OUT_DIR + "/cell_labels.tif")) {
            print("FATAL: Stage 6 needs cell_labels — run Stage 3 first");
            exit("missing cell_labels");
        }
        open(OUT_DIR + "/cell_labels.tif"); rename("cell_labels");
    }
    if (!File.exists(OUT_DIR + "/gfp_positive_ids.txt")) {
        print("FATAL: Stage 6 needs gfp_positive_ids.txt — run Stage 4 first");
        exit("missing gfp_positive_ids.txt");
    }
    positives6 = File.openAsString(OUT_DIR + "/gfp_positive_ids.txt");
    while (lengthOf(positives6) > 0 && (endsWith(positives6, "\n") || endsWith(positives6, "\r") || endsWith(positives6, " "))) {
        positives6 = substring(positives6, 0, lengthOf(positives6) - 1);
    }
    if (lengthOf(positives6) == 0) {
        print("No GFP+ cells — skipping Stage 6");
    } else {

    print("GFP+ ids     : " + positives6);

    selectWindow("ch_gfp");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    // ── CLIP170 Ridge Detection parameters ──
    c_width_um  = paramNum("clip170.ridge_line_width_um");
    c_sigma     = paramNum("clip170.ridge_sigma");
    c_high      = paramNum("clip170.ridge_high_contrast");
    c_low       = paramNum("clip170.ridge_low_contrast");
    c_lowerT    = paramNum("clip170.ridge_lower_threshold");
    c_upperT    = paramNum("clip170.ridge_upper_threshold");
    c_minlen_um = paramNum("clip170.ridge_min_line_length_um");

    c_width_px  = c_width_um / pw;
    c_minlen_px = c_minlen_um / pw;

    print("CLIP170 RIDGE params:");
    print("  line_width   : " + c_width_um + " um (" + c_width_px + " px)");
    print("  sigma        : " + c_sigma);
    print("  contrast hi/lo: " + c_high + " / " + c_low);
    print("  gradT lo/hi  : " + c_lowerT + " / " + c_upperT);
    print("  min length   : " + c_minlen_um + " um (" + c_minlen_px + " px)");

    // ── Step 1: Prepare 8-bit GFP (global) ──
    selectWindow("ch_gfp");
    W6 = getWidth(); H6 = getHeight();
    run("Duplicate...", "title=gfp_for_ridges");
    getRawStatistics(gfp_n, gfp_mean, gfp_min, gfp_max);
    print("  raw GFP min/max/mean    : " + gfp_min + " / " + gfp_max + " / " + gfp_mean);
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    rename("gfp_8bit");

    // Save diagnostic
    run("Duplicate...", "title=gfp_8bit_save");
    saveAs("PNG", OUT_DIR + "/_06_gfp_8bit.png");
    close();
    selectWindow("gfp_8bit");

    // Empty global ridges accumulator
    newImage("clip170_ridges", "8-bit black", W6, H6, 1);
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);

    // CSV of per-cell metrics
    clip_csv = "cell_id,is_gfp_positive,clip170_ridge_length_um,clip170_ridge_fg_px\n";

    pos_padded6 = "," + positives6 + ",";
    selectWindow("cell_labels");
    getRawStatistics(_d1, _d2, _d3, n_cells_s6);
    n_cells_s6 = round(n_cells_s6);

    margin6 = 10;
    total_ridge_um = 0;
    t_clip_all = getTime();

    for (cid6 = 1; cid6 <= n_cells_s6; cid6++) {
        if (indexOf(pos_padded6, "," + cid6 + ",") < 0) {
            clip_csv = clip_csv + cid6 + ",0,,\n";
            continue;
        }

        t_cell_6 = getTime();

        // Get bbox from cell_labels
        selectWindow("cell_labels");
        setThreshold(cid6, cid6);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            clip_csv = clip_csv + cid6 + ",1,0,0\n";
            continue;
        }
        getSelectionBounds(bx6, by6, bw6, bh6);
        x06 = bx6 - margin6; if (x06 < 0) x06 = 0;
        y06 = by6 - margin6; if (y06 < 0) y06 = 0;
        x16 = bx6 + bw6 + margin6; if (x16 > W6) x16 = W6;
        y16 = by6 + bh6 + margin6; if (y16 > H6) y16 = H6;
        cw6 = x16 - x06;
        ch6 = y16 - y06;

        // Crop the 8-bit GFP
        selectWindow("gfp_8bit");
        makeRectangle(x06, y06, cw6, ch6);
        run("Duplicate...", "title=crop_gfp");
        run("Select None");

        // Build per-cell mask (from cell_labels crop)
        selectWindow("cell_labels");
        makeRectangle(x06, y06, cw6, ch6);
        run("Duplicate...", "title=crop_lbl6");
        run("Select None");
        setThreshold(cid6, cid6);
        run("Convert to Mask");
        rename("crop_cell_mask6");

        // Zero out GFP outside the cell
        imageCalculator("Min create", "crop_gfp", "crop_cell_mask6");
        rename("crop_gfp_masked");
        close("crop_gfp");
        close("crop_cell_mask6");

        // Run Ridge Detection
        selectWindow("crop_gfp_masked");
        run("Ridge Detection",
            "line_width=" + c_width_px +
            " high_contrast=" + c_high +
            " low_contrast=" + c_low +
            " lower_threshold=" + c_lowerT +
            " upper_threshold=" + c_upperT +
            " minimum_line_length=" + c_minlen_px +
            " sigma=" + c_sigma +
            " make_binary" +
            " method_for_overlap_resolution=SLOPE");

        // Find the Detected segments window
        segs_title6 = "";
        list6 = getList("image.titles");
        for (lix = 0; lix < list6.length; lix++) {
            if (indexOf(list6[lix], "Detected segments") >= 0) {
                segs_title6 = list6[lix];
                lix = list6.length;
            }
        }
        if (segs_title6 == "") {
            print("  cell " + cid6 + ": Ridge Detection produced no output, skipping");
            close("crop_gfp_masked*");
            close("crop_lbl6*");
            clip_csv = clip_csv + cid6 + ",1,0,0\n";
            continue;
        }

        // MAX-merge into global clip170_ridges at (x06, y06)
        selectWindow("clip170_ridges");
        makeRectangle(x06, y06, cw6, ch6);
        run("Duplicate...", "title=existing6");
        run("Select None");
        imageCalculator("Max", "existing6", segs_title6);
        selectWindow("existing6");
        run("Select All");
        run("Copy");
        selectWindow("clip170_ridges");
        makeRectangle(x06, y06, cw6, ch6);
        run("Paste");
        run("Select None");
        close("existing6");
        close(segs_title6);
        close("crop_gfp_masked*");
        close("crop_lbl6*");

        dtc6 = (getTime() - t_cell_6) / 1000;
        print("  cell " + cid6 + ": bbox " + cw6 + "x" + ch6 + " in " + dtc6 + "s");
    }
    print("  per-cell CLIP170 Ridge Detection total : " + ((getTime() - t_clip_all) / 1000) + " s");

    // Now measure per-cell clip170 ridge length by intersecting global
    // clip170_ridges with each cell label.
    total_ridge_um = 0;
    // Reopen csv builder (rewrite for accurate counts)
    clip_csv = "cell_id,is_gfp_positive,clip170_ridge_length_um,clip170_ridge_fg_px\n";
    for (cid6 = 1; cid6 <= n_cells_s6; cid6++) {
        if (indexOf(pos_padded6, "," + cid6 + ",") < 0) {
            clip_csv = clip_csv + cid6 + ",0,,\n";
            continue;
        }
        selectWindow("cell_labels");
        setThreshold(cid6, cid6);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            clip_csv = clip_csv + cid6 + ",1,0,0\n";
            continue;
        }
        selectWindow("clip170_ridges");
        run("Restore Selection");
        getRawStatistics(clr_n, clr_mean);
        clr_fg_px = round(clr_mean * clr_n / 255);
        clr_len_um = clr_fg_px * pw;
        run("Select None");
        total_ridge_um = total_ridge_um + clr_len_um;
        clip_csv = clip_csv + cid6 + ",1," + clr_len_um + "," + clr_fg_px + "\n";
        print("  cell " + cid6 + ": " + clr_fg_px + " px = " + d2s(clr_len_um, 1) + " um");
    }
    print("TOTAL CLIP170 ridge length: " + d2s(total_ridge_um, 1) + " um");

    File.saveString(clip_csv, OUT_DIR + "/clip170_comets.csv");
    print("wrote        : clip170_comets.csv");

    // Save clip170_ridges as mask for Stage 7 (co-localization)
    selectWindow("clip170_ridges");
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/clip170_mask.tif");
    rename("clip170_ridges");
    // Also save with the "ridges" name for clarity
    run("Duplicate...", "title=clip170_ridges_save");
    saveAs("Tiff", OUT_DIR + "/clip170_ridges.tif");
    close();
    selectWindow("clip170_ridges");

    // ── QC composite: dim gray GFP + green CLIP170 ridges + yellow outlines
    selectWindow("cell_labels");
    run("Duplicate...", "title=cell_outline_16_6");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");
    run("Dilate");
    rename("cell_outline6");
    run("Multiply...", "value=0.6");

    // Dilate ridges for visibility
    selectWindow("clip170_ridges");
    run("Duplicate...", "title=clip_ridges_fat");
    run("Dilate");
    run("Dilate");

    selectWindow("ch_gfp");
    run("Duplicate...", "title=gfp_base");
    run("Enhance Contrast", "saturated=0.35");
    run("8-bit");
    run("Multiply...", "value=0.25");

    selectWindow("gfp_base");
    run("Duplicate...", "title=qc6_R");
    imageCalculator("Max", "qc6_R", "cell_outline6");
    selectWindow("gfp_base");
    run("Duplicate...", "title=qc6_G");
    imageCalculator("Max", "qc6_G", "clip_ridges_fat");
    imageCalculator("Max", "qc6_G", "cell_outline6");
    selectWindow("gfp_base");
    run("Duplicate...", "title=qc6_B");

    run("Merge Channels...", "c1=qc6_R c2=qc6_G c3=qc6_B create");
    run("RGB Color");
    rename("qc_clip");

    // Per-cell labels with ridge length
    setFont("SansSerif", 26, "bold");
    setColor(255, 255, 0);
    for (lid = 1; lid <= n_cells_s6; lid++) {
        if (indexOf(pos_padded6, "," + lid + ",") >= 0) {
            selectWindow("cell_labels");
            setThreshold(lid, lid);
            run("Create Selection");
            resetThreshold();
            if (selectionType() >= 0) {
                getSelectionBounds(bxl, byl, bwl, bhl);
                cxl = bxl + bwl / 2;
                cyl = byl + bhl / 2;
                selectWindow("clip170_ridges");
                run("Restore Selection");
                getRawStatistics(ln_n, ln_mean);
                lfg = round(ln_mean * ln_n / 255);
                llen = lfg * pw;
                run("Select None");
                selectWindow("qc_clip");
                drawString(lid + ":" + round(llen) + "um", cxl - 40, cyl + 10);
            }
        }
    }

    selectWindow("qc_clip");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/06_clip170_comets.png");
    print("wrote        : 06_clip170_comets.png");

    // Save ridges-only view
    selectWindow("clip170_ridges");
    run("Duplicate...", "title=clip_only");
    run("RGB Color");
    saveAs("PNG", OUT_DIR + "/06_clip170_ridges_only.png");
    close();

    close("qc6*");
    close("qc_clip*");
    close("cell_outline6*");
    close("cell_outline_16_6*");
    close("clip_ridges_fat*");
    close("gfp_base*");
    close("gfp_for_ridges*");
    close("gfp_8bit*");
    close("clip_only*");

    } // end if positives non-empty
    print("stage 6 done : " + ((getTime() - t6) / 1000) + "s");
}
// ============================================================
// ── STAGE 7 · MT / CLIP170 co-localization ──────────────────
// ============================================================
if (shouldRun(7)) {
    stageBanner(7, "MT / CLIP170 co-localization");
    t7 = getTime();

    // Skip gracefully if no GFP+ cells (Stage 5/6 produced no ridge masks)
    if (!File.exists(OUT_DIR + "/gfp_positive_ids.txt")) {
        print("FATAL: Stage 7 needs gfp_positive_ids.txt");
        exit("missing gfp_positive_ids");
    }
    positives7 = File.openAsString(OUT_DIR + "/gfp_positive_ids.txt");
    while (lengthOf(positives7) > 0 && (endsWith(positives7, "\n") || endsWith(positives7, "\r") || endsWith(positives7, " "))) {
        positives7 = substring(positives7, 0, lengthOf(positives7) - 1);
    }
    if (lengthOf(positives7) == 0) {
        print("No GFP+ cells — skipping Stage 7");
        // Still write an empty coloc.csv so Stage 8 finds it
        File.saveString("cell_id,is_gfp_positive,mt_length_um,clip170_length_um,coloc_length_um,clip170_on_mt_fraction,mt_with_clip170_fraction\n", OUT_DIR + "/coloc.csv");
        print("stage 7 done : " + ((getTime() - t7) / 1000) + "s");
    } else {

    // Load required masks
    if (!isOpen("mt_ridges")) {
        if (!File.exists(OUT_DIR + "/mt_ridges.tif")) {
            print("FATAL: Stage 7 needs mt_ridges.tif — run Stage 5 first");
            exit("missing mt_ridges");
        }
        open(OUT_DIR + "/mt_ridges.tif"); rename("mt_ridges");
    }
    if (!isOpen("clip170_ridges")) {
        if (!File.exists(OUT_DIR + "/clip170_ridges.tif")) {
            print("FATAL: Stage 7 needs clip170_ridges.tif — run Stage 6 first");
            exit("missing clip170_ridges");
        }
        open(OUT_DIR + "/clip170_ridges.tif"); rename("clip170_ridges");
    }
    if (!isOpen("cell_labels")) {
        open(OUT_DIR + "/cell_labels.tif"); rename("cell_labels");
    }

    selectWindow("mt_ridges");
    getPixelSize(unit, pw, ph);
    if (pw <= 0) pw = PX_UM;

    mt_dilation_um = paramNum("coloc.mt_dilation_um");
    if (isNaN(mt_dilation_um)) mt_dilation_um = 0.3;
    mt_dilation_px = round(mt_dilation_um / pw);
    if (mt_dilation_px < 1) mt_dilation_px = 1;
    print("MT dilation  : " + mt_dilation_um + " um = " + mt_dilation_px + " px");

    // ── Step 1: Build dilated MT neighborhood mask ──
    // Use Maximum filter (radius-based, unambiguous direction) instead of
    // run("Dilate") which depends on the BlackBackground option.
    selectWindow("mt_ridges");
    getRawStatistics(_mr_n, _mr_mean);
    print("  mt_ridges fg : " + round(_mr_mean * _mr_n / 255));
    run("Duplicate...", "title=mt_neighborhood");
    run("Maximum...", "radius=" + mt_dilation_px);
    selectWindow("mt_neighborhood");
    getRawStatistics(_mn_n, _mn_mean);
    print("  mt_neighborhood fg (after Maximum r=" + mt_dilation_px + "): " + round(_mn_mean * _mn_n / 255));

    selectWindow("clip170_ridges");
    getRawStatistics(_cr_n, _cr_mean);
    print("  clip170_ridges fg : " + round(_cr_mean * _cr_n / 255));

    // ── Step 2: Build coloc mask = clip170_ridges ∩ mt_neighborhood ──
    // Use AND via imageCalculator. Note: "AND" operates bitwise on 8-bit
    // images, which on 0/255 binary is equivalent to logical AND.
    selectWindow("clip170_ridges");
    run("Duplicate...", "title=coloc_mask");
    imageCalculator("AND", "coloc_mask", "mt_neighborhood");
    selectWindow("coloc_mask");
    getRawStatistics(_co_n, _co_mean);
    print("  coloc_mask fg (after AND): " + round(_co_mean * _co_n / 255));

    // Save for inspection
    selectWindow("coloc_mask");
    run("Properties...", "unit=" + unit + " pixel_width=" + pw + " pixel_height=" + pw);
    saveAs("Tiff", OUT_DIR + "/coloc_mask.tif");
    rename("coloc_mask");

    // Also a CLIP170-OFF-MT mask = clip170_ridges - coloc
    imageCalculator("Subtract create", "clip170_ridges", "coloc_mask");
    rename("clip170_off_mt");

    // ── Step 3: Per-cell metrics ──
    pos_padded7 = "," + positives7 + ",";
    selectWindow("cell_labels");
    getRawStatistics(_a, _b, _c, n_cells_s7);
    n_cells_s7 = round(n_cells_s7);

    coloc_csv = "cell_id,is_gfp_positive,mt_length_um,clip170_length_um,coloc_length_um,clip170_on_mt_fraction,mt_with_clip170_fraction\n";

    total_mt = 0;
    total_clip = 0;
    total_coloc = 0;

    for (cid7 = 1; cid7 <= n_cells_s7; cid7++) {
        if (indexOf(pos_padded7, "," + cid7 + ",") < 0) {
            coloc_csv = coloc_csv + cid7 + ",0,,,,,\n";
            continue;
        }

        selectWindow("cell_labels");
        setThreshold(cid7, cid7);
        run("Create Selection");
        resetThreshold();
        if (selectionType() < 0) {
            coloc_csv = coloc_csv + cid7 + ",1,0,0,0,0,0\n";
            continue;
        }

        // MT length inside cell
        selectWindow("mt_ridges");
        run("Restore Selection");
        getRawStatistics(mt_n, mt_mean);
        mt_fg = round(mt_mean * mt_n / 255);
        mt_um = mt_fg * pw;
        run("Select None");

        // CLIP170 length inside cell
        selectWindow("clip170_ridges");
        setThreshold(0, 0);  // no-op to clear
        resetThreshold();
        selectWindow("cell_labels");
        setThreshold(cid7, cid7);
        run("Create Selection");
        resetThreshold();
        selectWindow("clip170_ridges");
        run("Restore Selection");
        getRawStatistics(cl_n, cl_mean);
        cl_fg = round(cl_mean * cl_n / 255);
        cl_um = cl_fg * pw;
        run("Select None");

        // Coloc length inside cell
        selectWindow("cell_labels");
        setThreshold(cid7, cid7);
        run("Create Selection");
        resetThreshold();
        selectWindow("coloc_mask");
        run("Restore Selection");
        getRawStatistics(co_n, co_mean);
        co_fg = round(co_mean * co_n / 255);
        co_um = co_fg * pw;
        run("Select None");

        clip_on_mt_frac = 0;
        if (cl_fg > 0) clip_on_mt_frac = co_fg / cl_fg;
        // For "MT decorated with CLIP170": dilate clip170 instead, intersect with mt_ridges.
        // For now report a proxy = co_fg / mt_fg (assumes coloc detected on MT side ≈ on clip side)
        mt_with_clip_frac = 0;
        if (mt_fg > 0) mt_with_clip_frac = co_fg / mt_fg;

        total_mt    = total_mt + mt_fg;
        total_clip  = total_clip + cl_fg;
        total_coloc = total_coloc + co_fg;

        print("  cell " + cid7 + ": MT " + d2s(mt_um, 1) + " um, CLIP " + d2s(cl_um, 1)
              + " um, coloc " + d2s(co_um, 1) + " um, clip-on-mt " + d2s(100 * clip_on_mt_frac, 1) + "%");

        coloc_csv = coloc_csv + cid7 + ",1," + mt_um + "," + cl_um + "," + co_um
                  + "," + clip_on_mt_frac + "," + mt_with_clip_frac + "\n";
    }

    print("TOTAL: MT " + d2s(total_mt * pw, 1) + " um, CLIP " + d2s(total_clip * pw, 1)
          + " um, coloc " + d2s(total_coloc * pw, 1) + " um");
    if (total_clip > 0) print("Global CLIP-on-MT fraction: " + d2s(100 * total_coloc / total_clip, 1) + " %");

    File.saveString(coloc_csv, OUT_DIR + "/coloc.csv");
    print("wrote        : coloc.csv");

    // ── QC composite: R = mt_ridges, G = clip170_ridges
    //   coloc pixels = R+G = yellow
    //   off-MT clip170 = green only
    //   off-CLIP MT = red only
    selectWindow("cell_labels");
    run("Duplicate...", "title=cell_outline_16_7");
    run("Find Edges");
    setThreshold(1, 65535);
    setOption("BlackBackground", true);
    run("Convert to Mask");
    run("Dilate");
    rename("cell_outline7");
    run("Multiply...", "value=0.5");

    // Fat versions for visibility
    selectWindow("mt_ridges");
    run("Duplicate...", "title=mt_fat");
    run("Dilate");

    selectWindow("clip170_ridges");
    run("Duplicate...", "title=clip_fat");
    run("Dilate");

    // R = mt_fat + outline
    selectWindow("mt_fat");
    run("Duplicate...", "title=qc7_R");
    imageCalculator("Max", "qc7_R", "cell_outline7");

    // G = clip_fat + outline
    selectWindow("clip_fat");
    run("Duplicate...", "title=qc7_G");
    imageCalculator("Max", "qc7_G", "cell_outline7");

    // B = zero
    selectWindow("mt_fat");
    run("Duplicate...", "title=qc7_B");
    run("Multiply...", "value=0");

    run("Merge Channels...", "c1=qc7_R c2=qc7_G c3=qc7_B create");
    run("RGB Color");
    rename("qc_coloc");

    // Per-cell labels with coloc fraction
    setFont("SansSerif", 26, "bold");
    setColor(255, 255, 255);
    for (lid = 1; lid <= n_cells_s7; lid++) {
        if (indexOf(pos_padded7, "," + lid + ",") >= 0) {
            selectWindow("cell_labels");
            setThreshold(lid, lid);
            run("Create Selection");
            resetThreshold();
            if (selectionType() >= 0) {
                getSelectionBounds(bxl, byl, bwl, bhl);
                cxl = bxl + bwl / 2;
                cyl = byl + bhl / 2;
                // Recompute fraction for this cell
                selectWindow("clip170_ridges");
                run("Restore Selection");
                getRawStatistics(_n1, _m1);
                cl_fg2 = round(_m1 * _n1 / 255);
                run("Select None");

                selectWindow("cell_labels");
                setThreshold(lid, lid);
                run("Create Selection");
                resetThreshold();
                selectWindow("coloc_mask");
                run("Restore Selection");
                getRawStatistics(_n2, _m2);
                co_fg2 = round(_m2 * _n2 / 255);
                run("Select None");

                frac2 = 0;
                if (cl_fg2 > 0) frac2 = 100 * co_fg2 / cl_fg2;
                selectWindow("qc_coloc");
                drawString(lid + ":" + round(frac2) + "%", cxl - 30, cyl + 10);
            }
        }
    }

    selectWindow("qc_coloc");
    run("Scale Bar...", "width=20 height=6 font=18 color=White background=None location=[Lower Right] overlay");
    run("Flatten");
    saveAs("PNG", OUT_DIR + "/07_colocalization.png");
    print("wrote        : 07_colocalization.png");

    close("qc7*");
    close("qc_coloc*");
    close("cell_outline7*");
    close("cell_outline_16_7*");
    close("mt_fat*");
    close("clip_fat*");
    close("mt_neighborhood*");
    close("clip170_off_mt*");

    print("stage 7 done : " + ((getTime() - t7) / 1000) + "s");
    } // end if positives non-empty
}
// ============================================================
// ── STAGE 8 · finalize marker ───────────────────────────────
// ============================================================
// Actual merging of per-stage CSVs into cells.csv + summary.csv happens
// in the Python driver (driver.py) after this macro returns. Keeping a
// no-op here so --stages 8-8 still produces a valid run.
if (shouldRun(8)) {
    stageBanner(8, "finalize");
    print("Stage 8 finalize is performed Python-side after the macro completes.");
}

print("");
print("=== pipeline done ===");
eval("script", "System.exit(0);");
