# Change: split names and allow several phone numbers

The address book is changing shape in three steps. They depend on each other: do them in this order.

## Old and new shapes

Old file (version 1): a bare JSON list of `{"name": "Ada Lovelace", "phone": "555-0100", "email": null}`.

New file (version 2): `{"version": 2, "contacts": [ ... ]}` where each record is

```
{"first": "Ada", "last": "Lovelace", "phones": ["555-0100"], "email": null}
```

Name rule (used for new records and for migrated ones): strip the name, split it on whitespace; the **last** word is
`last`, everything before it (joined with single spaces) is `first`. A one-word name is all `first`, with `last` as `""`.
So "Mary Ann Smith" is first "Mary Ann", last "Smith"; "Cher" is first "Cher", last "".
`phones` is `[]` when there is no phone (a missing, `null` or empty phone), else a list holding the one number.
`email` is kept as it is (`null` or the text).

## Step 1: the new shape (`contacts/store.py`)

- `make_record(name, phone=None, email=None)` returns a new-shape record by the name rule above.
- `save(path, records)` writes the version 2 file; `load(path)` returns the list of records from a version 2 file
  (`[]` if the file does not exist). `add` keeps working and appends a new-shape record.
- `load` on an old (version 1) file must not guess: it raises `store.OldFormatError` (a subclass of `Exception`).

## Step 2: migrate the old data (`contacts/migrate.py`)

- `migrate_file(path)` converts a version 1 file to version 2 in place, by the name rule, keeping the order of records.
  Before it writes, it copies the old file to `<path>.v1.bak` (for `data/contacts.json`: `data/contacts.json.v1.bak`).
  It returns `True` when it migrated.
- On a file that is already version 2 it changes nothing (no new backup either) and returns `False`.
- A file that does not exist: `migrate_file` returns `False`.
- Run it on the repository's own `data/contacts.json`, so the file in the repository is version 2 afterwards and the
  `.v1.bak` next to it holds the original.

## Step 3: the report (`contacts/report.py`)

`render(path)` reads the migrated data and returns text (lines joined with `\n`, no trailing newline):

- No contacts: `(no contacts)`.
- Contacts are grouped by the upper-case first letter of `last`; for a record with an empty `last`, of `first`.
- Groups are in alphabetical order of the letter; each group starts with a line `<LETTER> (<count>)`.
- Inside a group, records are ordered by `last` then `first`, both compared in lower case (ties keep file order).
- A record line is two spaces, then `<last>, <first>` (just `<first>` when `last` is empty); then, when there are
  phones, ` - ` and the phones joined by `, `.

`render` on a file that is still version 1 raises `store.OldFormatError` (do not migrate silently).
