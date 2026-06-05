from deformentor_cli.errors import FrejaError, FrejaRejectedError, FrejaTimeoutError
from deformentor_cli.freja import freja_login
from stockholm_freja import (
    FrejaError as SharedFrejaError,
    FrejaRejectedError as SharedFrejaRejectedError,
    FrejaTimeoutError as SharedFrejaTimeoutError,
    freja_login as shared_freja_login,
)


def test_freja_shim_reexports_shared_login():
    assert freja_login is shared_freja_login


def test_error_module_reexports_shared_freja_exceptions():
    assert FrejaError is SharedFrejaError
    assert FrejaRejectedError is SharedFrejaRejectedError
    assert FrejaTimeoutError is SharedFrejaTimeoutError
