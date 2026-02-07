/**
 * Recruiter Tracker - Google Apps Script Web App
 *
 * Deploy as: Web App -> Execute as "Me" -> Access "Anyone"
 *
 * Column layout:
 *   A = Ticket #
 *   B = Type (New, Left, Kicked, etc.)
 *   C = IGN
 *   D = Recruiter
 *   E = Manatee Promo (checkbox)
 *   F = Piranha Promo (checkbox)
 *   G = Paid (NYP, Paid, NP, LG)
 */

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var action = payload.action;

    switch (action) {
      case "addRow":
        return jsonResponse(addRow(payload));
      case "updateType":
        return jsonResponse(updateType(payload));
      case "updatePaid":
        return jsonResponse(updatePaid(payload));
      case "updatePromo":
        return jsonResponse(updatePromo(payload));
      case "findByIGN":
        return jsonResponse(findByIGN(payload));
      default:
        return jsonResponse({ success: false, error: "Unknown action: " + action });
    }
  } catch (err) {
    return jsonResponse({ success: false, error: err.toString() });
  }
}

function doGet(e) {
  return jsonResponse({ success: true, message: "Recruiter Tracker is running." });
}

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Sheet1");
}

/**
 * Add a new row to the spreadsheet.
 * payload: { ticket, type, ign, recruiter }
 */
function addRow(payload) {
  var sheet = getSheet();
  var ticket = payload.ticket || "";
  var type = payload.type || "New";
  var ign = payload.ign || "";
  var recruiter = payload.recruiter || "";

  sheet.appendRow([ticket, type, ign, recruiter, false, false, "NYP"]);
  return { success: true };
}

/**
 * Update the Type column for a given IGN.
 * payload: { ign, type }
 */
function updateType(payload) {
  var row = findRowByIGN(payload.ign);
  if (!row) return { success: false, error: "IGN not found: " + payload.ign };

  var sheet = getSheet();
  sheet.getRange(row, 2).setValue(payload.type);
  return { success: true };
}

/**
 * Update the Paid column for a given IGN.
 * payload: { ign, paid }
 */
function updatePaid(payload) {
  var row = findRowByIGN(payload.ign);
  if (!row) return { success: false, error: "IGN not found: " + payload.ign };

  var sheet = getSheet();
  sheet.getRange(row, 7).setValue(payload.paid);
  return { success: true };
}

/**
 * Update a promo column for a given IGN.
 * payload: { ign, promo } where promo is "manateePromo" or "piranhaPromo"
 */
function updatePromo(payload) {
  var row = findRowByIGN(payload.ign);
  if (!row) return { success: false, error: "IGN not found: " + payload.ign };

  var sheet = getSheet();
  var col;
  if (payload.promo === "manateePromo") {
    col = 5; // Column E
  } else if (payload.promo === "piranhaPromo") {
    col = 6; // Column F
  } else {
    return { success: false, error: "Unknown promo type: " + payload.promo };
  }

  sheet.getRange(row, col).setValue(true);
  return { success: true };
}

/**
 * Find a row by IGN and return its data.
 * payload: { ign }
 */
function findByIGN(payload) {
  var row = findRowByIGN(payload.ign);
  if (!row) return { success: true, data: null };

  var sheet = getSheet();
  var values = sheet.getRange(row, 1, 1, 7).getValues()[0];
  return {
    success: true,
    data: {
      ticket: values[0],
      type: values[1],
      ign: values[2],
      recruiter: values[3],
      manateePromo: values[4],
      piranhaPromo: values[5],
      paid: values[6]
    }
  };
}

/**
 * Search bottom-to-top for the most recent row matching IGN (case-insensitive).
 * Returns the 1-based row number, or null if not found.
 */
function findRowByIGN(ign) {
  if (!ign) return null;

  var sheet = getSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return null; // Only header or empty

  var ignCol = sheet.getRange(2, 3, lastRow - 1, 1).getValues(); // Column C, skip header
  var target = ign.toLowerCase();

  for (var i = ignCol.length - 1; i >= 0; i--) {
    if (String(ignCol[i][0]).toLowerCase() === target) {
      return i + 2; // Convert to 1-based row (accounting for header)
    }
  }
  return null;
}
