"""Record layouts of ExpressNet 3 (Protel) data files, as far as they are known.

Every decoder returns a dict of named fields plus 'raw' (the untouched record
bytes), so nothing is lost while the layouts are still being worked out.
Offsets are documented in FORMATS.md.
"""
from __future__ import annotations

import struct

from .fields import cstr, hhmm, iso, minutes_to_datetime, mmddyy, u8, u32, yymmdd, yyyymmdd
from .scramble import descramble_syspar, password_from_record

# Communication result codes (COMMERR.DAT byte 44), in ExpressNet's own order.
COMM_ERRORS = [
    "No error", "No site record", "Build parameters failed", "Database time-out",
    "Time-out on carrier detect", "BUSY detected", "No carrier detected", "Carrier, but no CONNECT",
    "Sync char not detected", "Status string not complete", "Status string checksum bad",
    "Phone number mismatch", "Phone type mismatch", "Security code command time-out",
    "Security code failed", "Clear flags failed, time-out", "Clear flags failed",
    "Failed SW version memory read", "Failed extended SW version", "Escrow relay check failure",
    "Full dwnld failure, time-out", "Full download failure", "Full download verify time-out",
    "Full download failure, verify", "Full dwnld flag reset time-out", "Full download time-out",
    "Full dwnld flag reset failed", "Partial download time-out", "Partial download failure",
    "Partial download time-out", "Partial download failure", "Partial dwnld verify time-out",
    "Partial dwnld verify failure", "Failed clr coin flag command", "Reset cash box volume failure",
    "Phone & file checksums differ", "Incompatible Firmware", "Get CDR counts time-out",
    "Billing CDR count error", "Audit CDR count error", "Get billing CDR rec failure",
    "Temp billing rec save failed", "Chksum error in billing rec", "Billing CDR not saved to file",
    "Err opening billing CDR file", "Bad billing CDR saved to file", "Failed to clear billing recs",
    "Failed to clear billing flags", "Get audit CDR record failure", "Temp audit record save failed",
    "Checksum error in audit rec", "Audit CDR not saved to file", "Error opening audit CDR file",
    "Failed to clear audit records", "Failed to clear audit flags", "No Download blocks found",
    "Error opening software file", "Unlock Download cmd time-out", "Unlock Download cmd failure",
    "Disable Download cmd time-out", "Disable Download cmd failure", "Failed to save coin record",
    "Software file not found", "Software file dwnld time-out", "Verify software failure",
    "Software dwnld cmd failure", "Chk call acct errors failure", "Get call acct failure",
    "Failed to save call acct", "Reset call accounting time-out", "Failed to save new call acct",
    "Reset call accounting failure", "Reset clock failure, time-out", "Reset clock failure",
    "Software file not assigned", "Dwnld complt, CA/DA time-out", "Dwnld incomplt, CA/DA time-out",
    "Failed to save speed dials", "Database read failure", "Database write failure",
    "Communication fails", "Not a valid command", "TX fails CRC", "RX fails CRC",
    "Serial# ID circuit fail", "Firmware verify time-out", "Not a valid response",
    "Failed Updating Rates", "DefaulCD.FIL file not found", "DefaulCD.FIL file ver fails",
    "DefaulCD.FIL file fails read", "Failed to clear Bill CDR Err", "Clear Call Acct RAM Error Flag",
    "Required DD rates do not exist", "CA/CD phone needs updating", "Failed Audit Lock",
    "Failed Bill Lock", "Failed Coin Lock", "Failed Call Act. Lock", "Call was stopped manually",
] + [f"{n} Not presently used" for n in range(100, 111)] + [
    "Voice download failure", "Communication not in Sync.", "ACDR count not in proper range",
    "Undefined Error",
]


# Trouble flags in a coin record (COIN.DAT bytes 48-56), as ExpressNet's own trouble report
# reads them: (code, name, byte offset in the record, bit mask). Order is ExpressNet's.
TROUBLE_FLAGS = [
    ("NE", "Audit CDR Err", 50, 0x40), ("BG", "Bad Ground", 52, 0x10), ("CE", "Bill CDR Err", 50, 0x80),
    ("CF", "Bill CDR Full", 49, 0x80), ("CL", "Bill CDR Limit", 49, 0x40), ("BE", "Box Amt Err", 49, 0x08),
    ("BM", "Box Missing", 53, 0x20), ("BR", "Box Removed", 53, 0x02), ("AE", "Call Acct Err", 49, 0x20),
    ("E$", "Cash Emptied", 48, 0x01), ("VE", "Cash Vol Err", 49, 0x01), ("$V", "Cash Vol Full", 48, 0x10),
    ("CR", "Clock Reset", 52, 0x20), ("$F", "Coin Box Full", 50, 0x02), ("CG", "Coin Gate Fail", 50, 0x04),
    ("ME", "Coin Mech Err", 50, 0x10), ("$E", "Coin Tot Err", 49, 0x02), ("CT", "Coin Tot Fix", 56, 0x02),
    ("DI", "Dial Inactive", 53, 0x40), ("EE", "Download Error", 52, 0x40), ("HG", "Handset Gone", 51, 0x80),
    ("HO", "Handset OffHk", 51, 0x10), ("DE", "Defect Escrow", 50, 0x08), ("FC", "Failed Comms", 56, 0x01),
    ("LR", "Loop Reversed", 48, 0x80), ("LB", "Low Battery", 51, 0x01), ("LA", "Lower Alarm", 51, 0x40),
    ("OC", "Opto-Coupler", 53, 0x01), ("OE", "Overage Err", 49, 0x10), ("PC", "Peg Count", 48, 0x08),
    ("PR", "Phone Repair", 48, 0x04), ("RE", "Ram Error", 48, 0x20), ("RC", "Relay Collect", 53, 0x08),
    ("RJ", "Relay Jam", 48, 0x40), ("RR", "Relay Refund", 53, 0x04), ("TE", "Reset Tot Err", 49, 0x04),
    ("SN", "Silicon Ser #", 53, 0x10), ("ST", "Status Check", 48, 0x02), ("TR", "Time Report", 50, 0x20),
    ("TG", "Trouble Gone", 52, 0x80), ("NF", "Audit CDR Full", 52, 0x01), ("SU", "Sig. Unit Bad", 53, 0x80),
    ("AC", "ASIC clk fail", 54, 0x02), ("CB", "CTB rate bad", 54, 0x01), ("TB", "Temp Brd fail", 54, 0x80),
]
TROUBLE_NAMES = {code: name for code, name, _, _ in TROUBLE_FLAGS}


def trouble_flags(r: bytes) -> list[str]:
    """Codes of the trouble flags set in a COIN.DAT record, e.g. ['E$', 'RJ']."""
    return [code for code, _, off, mask in TROUBLE_FLAGS if len(r) > off and r[off] & mask]


# Call-accounting categories (CALLACCT.DAT): each has (calls, money in cents, minutes) as three u16
# from byte 46, in this order. Labels follow ExpressNet's export, with two of its typos corrected.
CALL_CATEGORIES = (
    ["Local Coin", "IntraState IntraLata Coin", "IntraState InterLata Coin", "InterState IntraLata Coin",
     "InterState InterLata Coin", "Local Non Coin", "IntraState IntraLata Non Coin",
     "IntraState InterLata Non Coin", "InterState IntraLata Non Coin", "InterState InterLata Non Coin"]
    + [f"{n}11" for n in range(2, 10)]
    + ["Incoming Call"]
    + [f"Speed Dial *8{n}" for n in range(1, 10)] + ["Speed Dial *80", "Speed Dial *91", "Speed Dial *92"]
    + ["(1)555-XXXX", "(1)NPA-555-XXXX", "10XXX", "(1)800-NXX-XXXX", "PBX", "Free Number", "950-XXXX",
       "0-", "00", "0+7", "0+10"]
    + [f"Country Code #{n}" for n in range(1, 10)] + ["Country Code Dft"]
    + [f"Card Group {n}" for n in range(1, 16)]
    + ["*1 - *7"]
    + [f"ReRoute #{n}" for n in range(1, 16)]
)
CALL_ACCT_FIRST = 46


def call_accounting_counters(r: bytes) -> list[dict]:
    """The non-empty categories of a CALLACCT.DAT record: name, calls, money (cents), minutes."""
    out = []
    for i, name in enumerate(CALL_CATEGORIES):
        off = CALL_ACCT_FIRST + 6 * i
        if off + 6 > len(r):
            break
        calls, money, minutes = struct.unpack_from("<HHH", r, off)
        if calls or money or minutes:
            out.append({"category": name, "index": i, "calls": calls, "money_cents": money,
                        "minutes": minutes})
    return out


def call_detail(d: bytes) -> dict:
    """The 16 bytes after date/time in an audit call entry."""
    if d is None or len(d) < 16:
        return {"duration_s": None, "charged_cents": None, "collected_cents": None, "cost_band": None,
                "route": None}
    duration, charged, collected = struct.unpack_from("<III", d, 0)
    return {"duration_s": duration, "charged_cents": charged, "collected_cents": collected,
            "cost_band": d[12], "route": d[14]}


def comm_error_text(code: int) -> str:
    return COMM_ERRORS[code] if 0 <= code < len(COMM_ERRORS) else f"Unknown code {code}"


# --- the 44-byte header shared by the fixed-length CDR files ----------------------

def _cdr_header(r: bytes) -> dict:
    t = minutes_to_datetime(u32(r, 28))
    return {
        "group_no": cstr(r[1:8]),
        "site_no": cstr(r[8:17]),
        "phone": cstr(r[17:28]),
        "at": iso(t),
        "date_text": yymmdd(cstr(r[32:39])),
        "time_text": hhmm(cstr(r[39:44])),
    }


def coin_record(r: bytes) -> dict:
    """CDR/COIN.DAT (88 bytes): one row per successful poll."""
    d = _cdr_header(r)
    d.update({
        "status_bytes": r[56:64].hex(),           # 'PFlags'; byte 56 holds the FC and CT trouble flags
        "coin_total_cents": u32(r, 64),            # lifetime coin total reported by the phone
        "cash_box_cents": u32(r, 68),              # money currently in the cash box
        "raw": r,
    })
    d.update(coin_extras(r))
    return d


def coin_extras(r: bytes) -> dict:
    """Fields of a COIN.DAT record decoded after Milestone 1 (computed from the raw bytes)."""
    return {
        "billing_records": r[44], "audit_records": r[45],              # 🟡 counts the phone reported
        "billing_collected": r[46], "audit_collected": r[47],          # 🟡 records collected this poll
        "flags": trouble_flags(r),
        "amount_collected_cents": u32(r, 72) if len(r) >= 76 else 0,   # set when the box was emptied
        "amount_manual_cents": u32(r, 76) if len(r) >= 80 else 0,      # 🟡 entered by hand
    }


def comm_error_record(r: bytes) -> dict:
    """CDR/COMMERR.DAT (130 bytes): one row per failed (or logged) call to a phone."""
    d = _cdr_header(r)
    code = u8(r, 44)
    d.update({"code": code, "message": comm_error_text(code), "raw": r})
    return d


def call_accounting_record(r: bytes) -> dict:
    """CDR/CALLACCT.DAT (552 bytes): call counters for a period. Counters not yet mapped."""
    t = minutes_to_datetime(u32(r, 28))
    return {
        "group_no": cstr(r[1:8]),
        "site_no": cstr(r[8:17]),
        "phone": cstr(r[17:28]),
        "at": iso(t),
        "period_start": yymmdd(cstr(r[32:39])),
        "period_end": yymmdd(cstr(r[39:46])),
        "raw": r,
    }


AUDIT_HEADER = 42
AUDIT_ENTRY = 47


def audit_record(r: bytes) -> dict:
    """CDR/AUDITCDR.DAT (variable length): one poll's batch of individual calls."""
    t = minutes_to_datetime(u32(r, 28))
    n = r[41] if len(r) > 41 else 0
    calls = []
    for k in range(n):
        e = r[AUDIT_HEADER + AUDIT_ENTRY * k:AUDIT_HEADER + AUDIT_ENTRY * (k + 1)]
        if len(e) < AUDIT_ENTRY:
            break
        calls.append({
            "dialed": cstr(e[0:17]),
            "call_date": yyyymmdd(cstr(e[17:26])),
            "call_time": hhmm(cstr(e[26:31])),
            "detail": e[31:47],                    # 16 bytes; meaning still being mapped
        })
    return {
        "group_no": cstr(r[1:8]),
        "site_no": cstr(r[8:17]),
        "phone": cstr(r[17:28]),
        "at": iso(t),
        "date_text": yyyymmdd(cstr(r[32:41])),
        "declared_calls": n,
        "calls": calls,
        "size_ok": len(r) == AUDIT_HEADER + AUDIT_ENTRY * n,
        "raw_header": r[:AUDIT_HEADER],
    }


def site_record(r: bytes) -> dict:
    """SITES/SITES.DAT (2456 bytes): one payphone."""
    return {
        "phone": cstr(r[1:12]),
        "description": cstr(r[12:73]),
        "rate_record": cstr(r[86:99]),            # name of a PARAM/CALLCOST.DAT record
        "options_record": cstr(r[112:125]),       # name of a PARAM/MISCOPT.DAT record
        "group_no": cstr(r[383:390]),
        "site_no": cstr(r[390:399]),
        "speed": cstr(r[896:901]),
        "install_date": mmddyy(cstr(r[903:912])),
        "location_name": cstr(r[980:1011]),
        "address": cstr(r[1011:1042]),
        "city": cstr(r[1042:1063]),
        "state": cstr(r[1063:1066]),
        "zip": cstr(r[1066:1077]).rstrip("- "),
        "firmware_current": cstr(r[1250:1263]),
        "firmware_target": cstr(r[1263:1276]),
        "raw": r,
    }


def system_record(r: bytes) -> dict:
    """SYSTEM/X3SYSPAR.DAT (211 bytes, XOR-scrambled): install date, company, licence."""
    p = descramble_syspar(r)
    return {
        "install_date": mmddyy(cstr(p[1:10])),
        "company": cstr(p[19:50]),
        "license_no": cstr(p[50:61]),
        "raw": r,
    }


def modem_record(r: bytes) -> dict:
    """PARAM/MODEMS.DAT (230 bytes): modem profiles."""
    return {
        "name": cstr(r[1:22]),
        "description": cstr(r[22:63]),
        "init": cstr(r[63:104]),
        "answer": cstr(r[104:145]),
        "dial_1": cstr(r[145:166]),
        "dial_2": cstr(r[166:187]),
        "dial_3": cstr(r[187:208]),
        "reset": cstr(r[208:229]),
        "raw": r,
    }


def port_record(r: bytes) -> dict:
    """LOCAL/PORTS.DAT (313 bytes): one communication port and its modem settings."""
    return {
        "port_no": u8(r, 1),
        "modem_name": cstr(r[3:24]),
        "modem_description": cstr(r[24:65]),
        "init": cstr(r[65:106]),
        "answer": cstr(r[106:147]),
        "dial_1": cstr(r[147:168]),
        "dial_2": cstr(r[168:189]),
        "dial_3": cstr(r[189:210]),
        "reset": cstr(r[210:231]),
        "line_number": cstr(r[232:246]),
        "raw": r,
    }


def group_record(r: bytes) -> dict:
    """LOCAL/GROUPS.DAT (22 bytes)."""
    return {"group_no": cstr(r[1:8]), "raw": r}


def poll_list_record(r: bytes) -> dict:
    """LOCAL/POLLIST.DAT (231 bytes): polling schedules."""
    return {
        "name": cstr(r[1:14]),
        "description": cstr(r[14:75]),
        "start_time": cstr(r[79:85]),
        "raw": r,
    }


def user_record(r: bytes) -> dict:
    """SYSTEM/PASSWORD.DAT (83 bytes): ExpressNet logins."""
    return {
        "user": cstr(r[1:14]),
        "description": cstr(r[14:55]),
        "password": password_from_record(r),
        "raw": r,
    }


def named_record(r: bytes) -> dict:
    """PARAM/CALLCOST.DAT and PARAM/MISCOPT.DAT: only the name is decoded at import."""
    return {"name": cstr(r[1:14]), "raw": r}


# Security codes in an options record (PARAM/MISCOPT.DAT), 4 digits + NUL each, in the order
# ExpressNet's options editor asks for them.
SECURITY_CODES = [
    ("access", "Security access", 89),
    ("coin_collection", "Coin collection (*#1)", 94),
    ("coin_box_amount", "Coin box amount (*#4, *#5)", 99),
    ("cdr", "CDR retrieval", 104),
    ("credit_express", "Credit Express", 109),
]


def options_fields(r: bytes) -> dict:
    """Decoded fields of a PARAM/MISCOPT.DAT record (420 bytes): description and security codes."""
    d = {"description": cstr(r[14:75]) if len(r) >= 75 else ""}
    for key, _, off in SECURITY_CODES:
        d[f"code_{key}"] = cstr(r[off:off + 5]) if len(r) >= off + 5 else ""
    return d
