"""Invented layouts only: never load a device snapshot or pairing record."""
from datetime import datetime


def icon(bundle_id, **extra):
    return {"displayIdentifier": bundle_id, "displayName": bundle_id, **extra}


def folder(name, *members):
    return {"listType": "folder", "displayName": name, "iconLists": [list(members)]}


def state():
    return [
        [icon(f"test.dock.{i}") for i in range(4)],
        [folder("Tools", icon("test.existing.tool")),
         folder("Reading", icon("test.existing.reader"))],
        [icon("test.new.a", iconModDate=datetime(2026, 1, 2, 3, 4, 5)),
         icon("test.new.b")],
        [icon("test.new.c")],
    ]


def assignments():
    return {"test.new.a": "Tools", "test.new.b": "Reading", "test.new.c": "Tools"}
