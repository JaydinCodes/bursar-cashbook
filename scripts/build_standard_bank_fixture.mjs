import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputPath = "fixtures/standard_bank/standard_bank_business_xlsx_v1.xlsx";
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Transaction History");
sheet.showGridLines = false;
sheet.getRange("A1:F1").merge();
sheet.getRange("A1").values = [["Standard Bank Business Online - Transaction History"]];
sheet.getRange("A2:B3").values = [
  ["Account Name", "Example Public School Governing Body"],
  ["Period", "01 July 2026 - 31 July 2026"],
];
sheet.getRange("A5:F10").values = [
  ["Transaction Date", "Transaction Details", "Debits", "Credits", "Balance", "Reference"],
  [new Date("2026-07-01"), "Opening balance", null, null, 150000.00, null],
  [new Date("2026-07-02"), "EFT OUT NASHUA OFFICE SOLUTIONS", 4850.00, null, 145150.00, "SCHOOL-OPS-071"],
  [new Date("2026-07-03"), "EFT IN SCHOOL FEES JULY", null, 12000.00, 157150.00, "FEES-2026-07"],
  [new Date("2026-07-05"), "MONTHLY ACCOUNT FEE", 115.00, null, 157035.00, "BANK-FEE"],
  [new Date("2026-07-09"), "CASH DEPOSIT SPORT FUND", null, 2500.00, 159535.00, "SPORT-TERM3"],
];
sheet.getRange("A1:F1").format = { fill: "#0033A0", font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "center" };
sheet.getRange("A5:F5").format = { fill: "#D9E8FB", font: { bold: true, color: "#172033" }, borders: { preset: "outside", style: "thin", color: "#8AA7C7" } };
sheet.getRange("A6:A10").format.numberFormat = "yyyy-mm-dd";
sheet.getRange("C6:E10").format.numberFormat = 'R #,##0.00';
sheet.getRange("A1:F10").format.wrapText = true;
sheet.getRange("A1:A10").format.columnWidth = 18;
sheet.getRange("B1:B10").format.columnWidth = 38;
sheet.getRange("C1:E10").format.columnWidth = 14;
sheet.getRange("F1:F10").format.columnWidth = 20;
sheet.getRange("A1:F1").format.rowHeight = 28;
sheet.freezePanes.freezeRows(5);

const inspection = await workbook.inspect({ kind: "table", range: "Transaction History!A1:F10", include: "values,formulas", tableMaxRows: 10, tableMaxCols: 6 });
console.log(inspection.ndjson);
const image = await workbook.render({ sheetName: "Transaction History", range: "A1:F10", scale: 1.5, format: "png" });
await fs.writeFile("fixtures/standard_bank/standard_bank_business_xlsx_v1_preview.png", new Uint8Array(await image.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
