"""Build the bundled catalog from the exported app metadata, never via BLE."""

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIORITY = {name: rank for rank, name in enumerate(("signal", "counter", "parameter", "sample", "failure", "error", "warning"))}
LABELS = {
    (0x5031, 0): "Control temperature (AM040)",
    (0x4359, 0): "Heat pump return temperature",
    (0x501E, 0): "Outside temperature (AM027)",
    (0x4350, 0): "Compressor discharge temperature",
    (0x50A9, 0): "Internal appliance setpoint (AM101)",
    (0x434D, 0): "Indoor heat exchanger temperature",
    (0x434E, 0): "Outdoor evaporator temperature",
    (0x5404, 1): "Room temperature zone 1 (CM030)",
    (0x5013, 0): "Flow temperature (AM016)",
    (0x5302, 0): "Central heating setpoint (PM002)",
    (0x4301, 0): "Heat pump flow temperature (HM001)",
    (0x4302, 0): "Heat pump return temperature (HM002)",
}


def label_for(row):
    index, subindex = int(row["index_decimal"]), int(row["subindex"])
    if (index, subindex) in LABELS:
        return LABELS[index, subindex]
    description = " ".join(row["description"].split())
    if description:
        label = re.split(r"\.\s|\.$", description, maxsplit=1)[0]
        if len(label) > 95:
            label = label[:92].rsplit(" ", 1)[0] + "..."
    else:
        label = re.sub(r"^(var|par)", "", row["name"])
        label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label) or f"Register 0x{index:04X}"
    if "Zone" in row["name"] and subindex:
        label += f" - zone {subindex}"
    elif subindex:
        label += f" - subindex {subindex}"
    if row["code"]:
        label += f" ({row['code']})"
    return label


def build_catalog(csv_path, xml_path):
    document = ET.parse(xml_path).getroot()
    selections = {
        group.get("id"): {item.get("value"): item.get("result.txt", item.get("value")) for item in group}
        for group in document.iter("selections")
    }
    lengths = defaultdict(list)
    for entry in document.find("bdrcan").iter("sdo"):
        lengths[(int(entry.get("index"), 0), int(entry.get("subindex")))].append(int(entry.get("length")))
    grouped = defaultdict(list)
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source, delimiter=";"):
            grouped[(int(row["index_decimal"]), int(row["subindex"]))].append(row)
    registers = []
    for (index, subindex), variants in sorted(grouped.items()):
        row = min(variants, key=lambda item: PRIORITY[item["section"]])
        factor_match = re.search(r" x ([0-9.]+)$", row["expression"])
        factor = float(factor_match[1]) if factor_match else 1
        decimals = len(factor_match[1].split(".")[1]) if factor_match and "." in factor_match[1] else 0
        choices = selections.get(row["selection"], {})
        is_binary = row["selection"] in {"YesNo", "OffOn"} and set(choices) == {"0", "1"}
        registers.append({
            "node": 1,
            "index": index,
            "subindex": subindex,
            "name": label_for(row),
            "symbol": row["name"],
            "description": row["description"],
            "code": row["code"],
            "datatype": row["datatype"],
            "factor": factor,
            "decimals": decimals,
            "unit_id": row["unit_id"],
            "selection": row["selection"],
            "choices": choices,
            "binary": is_binary,
            "sections": sorted({item["section"] for item in variants}),
            "read_level": min(int(item["read_level"]) for item in variants if item["read_level"]),
            "length": max(lengths[(index, subindex)], default=0),
        })
    return {
        "schema_version": 1,
        "source": {"device": "EHC-07", "obd_version": "1.51", "cache": xml_path.stem},
        "registers": registers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-xml", type=Path, required=True)
    parser.add_argument("--csv", type=Path, default=ROOT / "elga_registers.csv")
    args = parser.parse_args()
    catalog = build_catalog(args.csv, args.metadata_xml)
    target = ROOT / "custom_components/elga_ace/register_catalog.json"
    target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(catalog['registers'])} unique read-only registers -> {target}")


if __name__ == "__main__":
    main()
