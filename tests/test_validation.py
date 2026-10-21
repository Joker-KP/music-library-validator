from music_validator.validation import validate

CONFIG = {
    "required_tags": ["title", "artist", "album", "tracknumber", "comment"],
    "artwork": {"required": True},
    "filename": {"regex": r"^\d{2} - .+$"},
    "album_rules": {"consistent_album": True, "unique_disc_track": True},
}


def test_missing_fields_and_cover():
    row = {
        "album_dir": "/music/A",
        "path": "/music/A/wrong.mp3",
        "metadata": {"tags": {"artist": "A"}, "artwork": []},
        "error": None,
        "disc_from_path": None,
    }
    rules = {i["rule"] for i in validate([row], CONFIG)}
    assert {
        "missing_title",
        "missing_album",
        "missing_tracknumber",
        "missing_comment",
        "missing_artwork",
        "filename_pattern",
    } <= rules


def test_disc_numbers_allow_repeated_track_numbers():
    rows = []
    for disc in (1, 2):
        rows.append(
            {
                "album_dir": "/music/A",
                "path": f"/music/A/CD{disc}/01 - Song.flac",
                "metadata": {
                    "tags": {
                        "title": "Song",
                        "artist": "A",
                        "album": "Album",
                        "tracknumber": "1",
                        "comment": "ok",
                    },
                    "artwork": [{"sha256": "x", "width": 600, "height": 600}],
                },
                "error": None,
                "disc_from_path": disc,
            }
        )
    assert not any(x["rule"] == "duplicate_track" for x in validate(rows, CONFIG))


def test_disc_tag_directory_mismatch_and_opt_out():
    row = {
        "album_dir": "/music/A",
        "path": "/music/A/CD2/01.flac",
        "metadata": {"tags": {"discnumber": "1/2", "tracknumber": "1"}, "artwork": []},
        "error": None,
        "disc_from_path": 2,
    }
    assert any(i["rule"] == "disc_number_mismatch" for i in validate([row], CONFIG))
    config = {**CONFIG, "album_rules": {"disc_number_mismatch": False}}
    assert not any(i["rule"] == "disc_number_mismatch" for i in validate([row], config))
