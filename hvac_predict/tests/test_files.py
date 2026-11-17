import pytest

from hvac_predict.errors import ConfigError
from hvac_predict.files import write_atomically


def test_a_file_is_replaced_whole_and_leaves_nothing_else_behind(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("old")

    write_atomically(path, "new")

    assert path.read_text() == "new"
    assert [each.name for each in tmp_path.iterdir()] == ["state.json"]


def test_a_planted_link_is_replaced_and_not_written_through(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("someone else's")
    path = tmp_path / "state.json"
    path.symlink_to(elsewhere)

    write_atomically(path, "new")

    assert not path.is_symlink()
    assert elsewhere.read_text() == "someone else's"


@pytest.mark.parametrize("obstacle", ["no-such-directory/state.json", "a-directory"])
def test_a_file_that_cannot_be_written_is_an_error_the_operator_can_read(tmp_path, obstacle):
    (tmp_path / "a-directory").mkdir()

    with pytest.raises(ConfigError, match=r"cannot write .*: "):
        write_atomically(tmp_path / obstacle, "new")

    assert [each.name for each in tmp_path.iterdir()] == ["a-directory"]
