// Check the running compositor before importing any version-coupled ELF plugin.
// Unknown, missing or ambiguous diagnostics must never authorize a load.
function compatible(report, expectedKwin, expectedQt) {
    if (typeof report !== "string" || report.length > 262144 ||
            !/^\d+\.\d+\.\d+$/.test(expectedKwin) ||
            !/^\d+\.\d+\.\d+$/.test(expectedQt)) return false;
    const kwin = report.match(/^KWin version:\s*(\S+)\s*$/gmi) || [];
    const qt = report.match(/^Qt Version:\s*(\S+)\s*$/gmi) || [];
    return kwin.length === 1 && qt.length === 1 &&
        kwin[0].split(":")[1].trim() === expectedKwin &&
        qt[0].split(":")[1].trim() === expectedQt;
}
if (typeof module !== "undefined") module.exports = {compatible};
