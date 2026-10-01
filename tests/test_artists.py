import os

from artists import ArtistIndex, normalize, split_alias
from scan import TagSet, Track


def tr(path, **tags):
    return Track(path, "MP3", 1, tags=TagSet(**tags))


def test_normalize():
    assert normalize("岡田 有希子") == normalize("岡田有希子")
    assert normalize("ＩＵ") == normalize("iu")  # full width -> half width, case
    assert split_alias("Yukiko Okada (岡田有希子)") == ("Yukiko Okada", "岡田有希子")
    assert split_alias("Minako Yoshida (吉田美奈子)") == ("Minako Yoshida", "吉田美奈子")
    assert split_alias("(G)I-DLE") == ("(G)I-DLE", "")
    assert split_alias("Artist (Live)") == ("Artist (Live)", "")


def test_spelling_and_bracket_alias_join():
    tracks = [tr("a.mp3", artist="岡田 有希子"), tr("b.mp3", artist="岡田 有希子"), tr("c.mp3", artist="岡田有希子"),
              tr("d.mp3", artist="Minako Yoshida"), tr("e.mp3", artist="Minako Yoshida (吉田美奈子)")]
    idx = ArtistIndex(tracks)
    assert idx.rep("岡田有希子") == "岡田 有希子"  # most files
    assert idx.rep("Minako Yoshida (吉田美奈子)") == "Minako Yoshida"


def test_alias_file_puts_three_spellings_in_one_folder():
    tracks = [tr("a.mp3", artist="岡田 有希子"), tr("b.mp3", artist="岡田有希子"), tr("c.mp3", artist="Yukiko Okada")]
    idx = ArtistIndex(tracks, {"岡田有希子": ["岡田 有希子", "Yukiko Okada"]})
    assert {idx.rep(t.tags.artist) for t in tracks} == {"岡田有希子"}


def test_preference_latin():
    tracks = [tr("a.mp3", artist="Yukiko Okada"), tr("b.mp3", artist="Yukiko Okada (岡田有希子)"), tr("c.mp3", artist="岡田有希子"),
              tr("d.mp3", artist="岡田有希子")]
    assert ArtistIndex(tracks, preference="original").rep("Yukiko Okada") == "岡田有希子"
    assert ArtistIndex(tracks, preference="latin").rep("岡田有希子") == "Yukiko Okada"


def test_artist_id_joins():
    tracks = [tr("a.mp3", artist="Okada", mb_artist_id="x1"), tr("b.mp3", artist="岡田", mb_artist_id="x1")]
    idx = ArtistIndex(tracks)
    assert idx.rep("Okada") == idx.rep("岡田")


def test_no_merge_splits_auto_group():
    tracks = [tr("a.mp3", artist="Ab C"), tr("b.mp3", artist="AbC")]
    assert ArtistIndex(tracks).rep("Ab C") == ArtistIndex(tracks).rep("AbC")
    idx = ArtistIndex(tracks, no_merge=["Ab C", "AbC"])
    assert idx.rep("Ab C") != idx.rep("AbC")


def test_guess_same_album_needs_confirmation():
    d = os.path.join("C:\\m", "album")
    tracks = [tr(os.path.join(d, "1.mp3"), artist="Yukiko Okada", album="Fairy"),
              tr(os.path.join(d, "2.mp3"), artist="오카다 유키코", album="Fairy"),
              tr(os.path.join(d, "3.mp3"), artist="Yukiko Okada & Someone", album="Fairy")]
    idx = ArtistIndex(tracks)
    assert len(idx.guesses) == 1
    assert sorted(idx.guesses[0].names) == sorted(["Yukiko Okada", "오카다 유키코"])
    assert idx.rep("오카다 유키코") == "오카다 유키코"  # not applied
    rejected = ArtistIndex(tracks, rejected=[idx.guesses[0].names])
    assert rejected.guesses == []
    confirmed = ArtistIndex(tracks, idx.aliases_with(idx.cluster_names_for(idx.guesses[0]), idx.guesses[0].proposed))
    assert confirmed.rep("오카다 유키코") == confirmed.rep("Yukiko Okada")


def test_compilation_is_not_guessed():
    d = os.path.join("C:\\m", "va")
    tracks = [tr(os.path.join(d, "1.mp3"), artist="A", album="Hits", album_artist="Various Artists"),
              tr(os.path.join(d, "2.mp3"), artist="B", album="Hits", album_artist="Various Artists")]
    assert ArtistIndex(tracks).guesses == []
