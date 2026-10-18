#!/usr/bin/env node
"use strict";

// Read-only text view for a Phase 3 snapshot. Window titles and app IDs are
// deliberately absent from its output.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const modelSource = fs.readFileSync(path.join(root, "core/model.js"), "utf8")
    .replace(/^\.pragma library\s*/m, "");
const registrySource = fs.readFileSync(path.join(root, "core/registry.js"), "utf8")
    .replace(/^\.pragma library\s*/m, "")
    .replace(/^\.import "model.js" as Model\s*/m, "");
const context = {};
vm.runInNewContext(modelSource
    + "\nthis.Model = { applicationExcluded, roundingContactAllowed, movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, validateSnapshot };\n"
    + registrySource
    + "\nthis.registryApi = { createRegistryState, reconcile, diagnosticSummary };", context);

function renderDiagnostic(input, selected) {
    input = input.trim();
    if (!input || input.length > 4_000_000) throw new Error("snapshot input is empty or too large");
    const marker = "OLED_WINDOW_GUARD_SNAPSHOT ";
    const lines = input.split(/\r?\n/);
    const marked = lines.filter(line => line.includes(marker));
    const payload = marked.length ? marked[marked.length - 1].split(marker, 2)[1] : input;
    const parsed = JSON.parse(payload);
    const snapshot = parsed.snapshot || parsed;
    const state = context.registryApi.reconcile(snapshot,
        context.registryApi.createRegistryState("diagnostic"),
        { sessionEpoch: "diagnostic", selectedOutputIds: selected || [] });
    const summary = context.registryApi.diagnosticSummary(state.windows);
    let view = `Eligible: ${summary.eligible}\nSkipped: ${summary.skipped}\nObstacles: ${summary.obstacles}\n`;
    for (const reason of Object.keys(summary.byReason).sort()) {
        view += `${reason}: ${summary.byReason[reason]}\n`;
    }
    return view;
}

function main(args) {
    const selected = [];
    let inputFile = null;
    for (let index = 0; index < args.length; index += 1) {
        if (args[index] === "--file" && index + 1 < args.length) inputFile = args[++index];
        else if (args[index] === "--select" && index + 1 < args.length) selected.push(args[++index]);
        else throw new Error("usage: diagnose_snapshot.js [--file SNAPSHOT] [--select OUTPUT_ID ...]");
    }
    process.stdout.write(renderDiagnostic(fs.readFileSync(inputFile || 0, "utf8"), selected));
}

module.exports = { renderDiagnostic };
if (require.main === module) main(process.argv.slice(2));
