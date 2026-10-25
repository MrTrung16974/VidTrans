import tempfile
import unittest
from pathlib import Path

from domain.comic import ComicCharacter, ComicScene, Storyboard
from infrastructure.comic_project import ComicProject
from infrastructure.comic_series import ComicSeriesStore


def board(*characters: ComicCharacter) -> Storyboard:
    return Storyboard(characters=list(characters), scenes=[ComicScene(
        scene_index=1, narration="Minh bước vào rừng.", visual_prompt="a forest",
        characters=[c.id for c in characters])])


MINH = ComicCharacter(id="minh", name="Minh", gender="male", appearance="young man, short black hair")


class ComicSeriesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = ComicSeriesStore(lambda owner: self.root / owner / "comic-series")
        self.series = self.store.create("u1", title="Tiên Nghịch", description="", settings={"style": "xianxia"})

    def project(self, name: str) -> ComicProject:
        return ComicProject(self.root / name)

    def test_episodes_are_numbered_and_owner_scoped(self) -> None:
        self.assertEqual(self.store.add_episode("u1", self.series.id, "aaaa0001"), 1)
        self.assertEqual(self.store.add_episode("u1", self.series.id, "aaaa0002"), 2)
        self.store.remove_episode("u1", self.series.id, "aaaa0002")
        self.assertEqual(self.store.load("u1", self.series.id).episode_job_ids(), ["aaaa0001"])
        self.assertIsNone(self.store.load("u2", self.series.id))
        self.assertIsNone(self.store.load("u1", "../u2"))
        self.assertEqual([s.id for s in self.store.list("u1")], [self.series.id])

    def test_first_episode_teaches_the_series_its_characters(self) -> None:
        episode = self.project("ep1")
        storyboard = episode.save_storyboard(board(MINH), bump=False)
        (episode.dir("refs") / "minh-0123.png").write_bytes(b"png")
        self.store.absorb_episode("u1", self.series.id, episode, storyboard, {"references": {"minh": "minh-0123.png"}})
        series = self.store.load("u1", self.series.id)
        self.assertEqual(series.character("minh").approved_reference, "minh-0123.png")
        self.assertTrue((series.root / "refs" / "minh-0123.png").is_file())

        # A later episode describing Minh differently never rewrites the series profile.
        redrawn = MINH.model_copy(update={"appearance": "old man"})
        self.store.absorb_episode("u1", self.series.id, episode, board(redrawn), {})
        self.assertEqual(self.store.load("u1", self.series.id).character("minh").appearance, MINH.appearance)

    def test_next_episode_uses_the_series_reference_unless_it_pinned_its_own(self) -> None:
        self.store.update("u1", self.series.id, lambda s: s.set_characters([
            MINH.model_copy(update={"approved_reference": "minh-upload-1.png"})]))
        (self.store.load("u1", self.series.id).refs_dir / "minh-upload-1.png").write_bytes(b"png")

        episode = self.project("ep2")
        storyboard = episode.save_storyboard(board(MINH), bump=False)
        synced = self.store.sync_into_episode("u1", self.series.id, episode, storyboard)
        self.assertEqual(synced.character("minh").approved_reference, "minh-upload-1.png")
        self.assertIsNotNone(episode.asset_path("refs", "minh-upload-1.png"))

        # The series switches to a new approved image: the episode follows it...
        (self.store.load("u1", self.series.id).refs_dir / "minh-upload-2.png").write_bytes(b"png")
        self.store.update("u1", self.series.id, lambda s: s.set_characters([
            MINH.model_copy(update={"approved_reference": "minh-upload-2.png"})]))
        synced = self.store.sync_into_episode("u1", self.series.id, episode, synced)
        self.assertEqual(synced.character("minh").approved_reference, "minh-upload-2.png")

        # ...but an image pinned inside the episode itself wins.
        (episode.dir("refs") / "minh-upload-own.png").write_bytes(b"png")
        own = synced.model_copy(deep=True)
        own.character("minh").approved_reference = "minh-upload-own.png"
        own = episode.save_storyboard(own)
        kept = self.store.sync_into_episode("u1", self.series.id, episode, own)
        self.assertEqual(kept.character("minh").approved_reference, "minh-upload-own.png")


if __name__ == "__main__":
    unittest.main()
