from axiom.db.base import (
    BaseDB,
)


def _lmdb_open_db_wrapper(db_path):
    """
    Open an LMDB environment and return a database handle compatible with LevelDB's API.

    LevelDB: leveldb.LevelDB(db_path, create_if_missing=True, error_if_exists=False)
    LMDB: Requires creating an Environment with map_size/max_dbs, then opening a database.
    This wrapper creates an LMDB environment with a default map_size of 1GB and max_dbs=1,
    opens the default database, and returns a handle that supports Get/Put/Delete operations
    via the wrapper functions below.
    """
    import leveldb


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: leveldb → lmdb
# The following functions bridge API incompatibilities between
# leveldb and lmdb. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _lmdb_get_wrapper(db_handle, key, default=None):
    """
    Execute LMDB Get within a read transaction and return bytes (or default if key missing).

    LevelDB: db.Get(key) returns bytes (or raises KeyError); db.Get(key, default=x) returns default.
    LMDB: Requires a read transaction; txn.get(key) returns bytes or None if missing.
    This wrapper opens a read transaction, retrieves the value, and returns it as bytes
    (or the provided default if the key doesn't exist).
    """
    with db_handle.begin() as txn:
        value = txn.get(key)
        if value is None:
            return default
        return value

def _lmdb_put_wrapper(db_handle, key, value):
    """
    Execute LMDB Put within a write transaction.

    LevelDB: db.Put(key, value) writes directly.
    LMDB: Requires a write transaction; txn.put(key, value) writes the value.
    This wrapper opens a write transaction and performs the put operation.
    """
    with db_handle.begin(write=True) as txn:
        txn.put(key, value)

def _lmdb_exists_wrapper(db_handle, key, default=False):
    """
    Execute LMDB Get within a read transaction, catch missing key, and return bool(result) or default.

    LevelDB: db.Get(key, default=False) returns the value or False; bool() converts to True/False.
    LMDB: txn.get(key) returns bytes or None.
    This wrapper checks if the key exists and returns True/False (or the provided default).
    """
    with db_handle.begin() as txn:
        value = txn.get(key)
        if value is None:
            return default
        return True

def _lmdb_delete_wrapper(db_handle, key):
    """
    Execute LMDB Delete within a write transaction.

    LevelDB: db.Delete(key) deletes directly.
    LMDB: Requires a write transaction; txn.delete(key) deletes the key.
    This wrapper opens a write transaction and performs the delete operation.
    """
    with db_handle.begin(write=True) as txn:
        txn.delete(key)

    env = lmdb.open(db_path, map_size=1024 * 1024 * 1024, max_dbs=1, create=True)
    return env


def _lmdb_get_wrapper(db_handle, key, default=None):
    """
    Execute LMDB Get within a read transaction and return bytes (or default if key missing).

    LevelDB: db.Get(key) returns bytes (or raises KeyError); db.Get(key, default=x) returns default.
    LMDB: Requires a read transaction; txn.get(key) returns bytes or None if missing.
    This wrapper opens a read transaction, retrieves the value, and returns it as bytes
    (or the provided default if the key doesn't exist).
    """
    with db_handle.begin() as txn:
        value = txn.get(key)
        if value is None:
            return default
        return value


def _lmdb_put_wrapper(db_handle, key, value):
    """
    Execute LMDB Put within a write transaction.

    LevelDB: db.Put(key, value) writes directly.
    LMDB: Requires a write transaction; txn.put(key, value) writes the value.
    This wrapper opens a write transaction and performs the put operation.
    """
    with db_handle.begin(write=True) as txn:
        txn.put(key, value)


def _lmdb_exists_wrapper(db_handle, key, default=False):
    """
    Execute LMDB Get within a read transaction, catch missing key, and return bool(result) or default.

    LevelDB: db.Get(key, default=False) returns the value or False; bool() converts to True/False.
    LMDB: txn.get(key) returns bytes or None.
    This wrapper checks if the key exists and returns True/False (or the provided default).
    """
    with db_handle.begin() as txn:
        value = txn.get(key)
        if value is None:
            return default
        return True


def _lmdb_delete_wrapper(db_handle, key):
    """
    Execute LMDB Delete within a write transaction.

    LevelDB: db.Delete(key) deletes directly.
    LMDB: Requires a write transaction; txn.delete(key) deletes the key.
    This wrapper opens a write transaction and performs the delete operation.
    """
    with db_handle.begin(write=True) as txn:
        txn.delete(key)


class LevelDB(BaseDB):

# Creates db as a class variable to avoid level db lock error
    def __init__(self, db_path=None):
        if not db_path:
            raise TypeError("Please specifiy a valid path for your database.")
        self.db_path = db_path
        self.db = _lmdb_open_db_wrapper(db_path)

    def get(self, key):
        # 'Get' Returns a bytearray which needs to be converted to straight bytes
        return bytes(_lmdb_get_wrapper(self.db, key))

    def set(self, key, value):
        _lmdb_put_wrapper(self.db, key, value)

    # Returns False instead of KeyError if key doesn't exist
    def exists(self, key):
        return bool(_lmdb_exists_wrapper(self.db, key, default=False))

    def delete(self, key):
        _lmdb_delete_wrapper(self.db, key)