/* Explicit adoption controls for possible pre-Ledgerly workbook rows. */
async function renderHistoricalMatches(plan) {
  const container = document.getElementById("syncPreviewBlocked");
  const matches = plan.historical_matches || [];
  if (!matches.length) return;
  container.innerHTML += matches.map(item => {
    const rows = item.matches.map(match =>
      `<tr><td>${escapeHtml(String(match.excel_row))}</td><td>${escapeHtml(match.narrative || "—")}</td>` +
      `<td>${escapeHtml(match.reference || "—")}</td><td>${escapeHtml(match.amount)}</td>` +
      `<td>${escapeHtml(match.category_matches ? "Category matches" : "Different category")}</td>` +
      `<td><button class="btn secondary small" data-adopt-transaction="${item.transaction_id}" data-adopt-row="${match.row_index}">Already in cashbook</button></td></tr>`
    ).join("");
    return `<div class="callout warning"><div><strong>Possible historical entry: ${escapeHtml(item.sheet_name)}</strong>` +
      `<p>Select the actual existing row. Ledgerly will validate it again and will not change Excel.</p>` +
      `<div class="table-wrap"><table><thead><tr><th>Row</th><th>Narrative</th><th>Reference</th><th>Amount</th><th>Allocation</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></div></div>`;
  }).join("");
  container.querySelectorAll("[data-adopt-transaction]").forEach(button => {
    button.addEventListener("click", async () => {
      if (!confirm("Confirm this selected row is already in the cashbook? Excel will not be changed.")) return;
      setBusy(button, true, "Confirming…");
      try {
        await api("/cashbook/adopt-existing-row", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({transaction_id: Number(button.dataset.adoptTransaction), row_index: Number(button.dataset.adoptRow)}),
        });
        toast("Existing cashbook row linked. It will not be appended.", "success");
        closeModal("syncPreviewModal");
        await Promise.all([loadCashbook(), loadOverview()]);
      } catch (error) {
        toast("The existing row could not be linked. " + error.message, "error");
      } finally { setBusy(button, false); }
    });
  });
}

const ledgerlyRunSync = runSync;
runSync = async function(button) {
  if (!state.cashbook?.registered) return ledgerlyRunSync(button);
  setBusy(button, true, "Checking…");
  try {
    const plan = await api("/cashbook/sync-preview");
    $("syncPreviewLead").textContent = plan.transactions.length + " transaction" + (plan.transactions.length === 1 ? " is" : "s are") + " ready to sync.";
    $("firstSyncNotice").classList.toggle("hidden", Number(state.cashbook.synced || 0) > 0);
    $("syncPreviewBlocked").innerHTML = (plan.blocked || []).length ? `<div class="callout error"><div><strong>Blocked transactions</strong><p>${plan.blocked.map(x => escapeHtml(x.reason)).join("<br>")}</p></div></div>` : "";
    $("syncPreviewRows").innerHTML = plan.transactions.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th>Sheet</th><th>Category</th></tr></thead><tbody>${plan.transactions.map(x => `<tr><td>${escapeHtml(x.date)}</td><td>${escapeHtml(x.cashbook_narrative)}</td><td>${escapeHtml(x.sheet_name)}</td><td>${escapeHtml(x.category)}</td></tr>`).join("")}</tbody></table></div>` : "";
    $("syncPreviewSummary").innerHTML = (plan.summary || []).map(x => `<span class="badge reviewed">${escapeHtml(x.sheet_name)}: ${x.transaction_count} · ${money(x.amount)}</span>`).join(" ");
    await renderHistoricalMatches(plan);
    $("confirmSync").disabled = !plan.ready || !plan.transactions.length;
    $("confirmSync").onclick = async () => { const result = await api("/cashbook/sync", {method: "POST"}); closeModal("syncPreviewModal"); toast(result.message, "success"); await Promise.all([loadCashbook(), loadOverview()]); };
    showModal("syncPreviewModal");
  } catch (error) { toast("Cashbook could not be checked. " + error.message, "error"); }
  finally { setBusy(button, false); }
};

// The persisted BursarCashbook path remains a compatibility identifier, but
// no visible application wording should expose the former product name.
function applyLedgerlyBranding() {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    node.nodeValue = node.nodeValue
      .replaceAll("Bursar Cashbook", "Ledgerly")
      .replaceAll("BursarCashbook application", "Ledgerly application");
  });
}
applyLedgerlyBranding();
new MutationObserver(applyLedgerlyBranding).observe(document.body, {childList: true, subtree: true});
