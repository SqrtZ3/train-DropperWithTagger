import unittest

from PIL import Image

from webapp.buckets import (
    choose_arb_bucket,
    downsample_preserving_detail,
    plan_arb_export,
    snap_to_texture_bucket,
    texture_buckets,
)
from webapp.routes_crop import _export_crop


class ArbExportPlanningTests(unittest.TestCase):
    def test_near_square_image_crops_to_bucket_without_resizing(self):
        plan = plan_arb_export(
            1053,
            1053,
            base_resos=[512, 768, 1024],
            min_reso=512,
            max_reso=2048,
            step=64,
            output_max_base_reso=1024,
        )

        self.assertEqual(plan.bucket_size, (1024, 1024))
        self.assertEqual(plan.crop_box, (14, 14, 1024, 1024))
        self.assertEqual(plan.output_size, (1024, 1024))
        self.assertFalse(plan.needs_resize)
        self.assertEqual(plan.action, "crop")

    def test_small_image_selects_smaller_bucket_instead_of_upscaling(self):
        bucket = choose_arb_bucket(
            900,
            900,
            base_resos=[512, 768, 1024],
            min_reso=512,
            max_reso=2048,
            step=64,
            no_upscale=True,
        )

        self.assertEqual(bucket, (768, 768))

    def test_tiny_image_stays_at_crop_size_instead_of_upscaling_to_min_bucket(self):
        plan = plan_arb_export(
            400,
            400,
            base_resos=[512, 768, 1024],
            min_reso=512,
            max_reso=2048,
            step=64,
            output_max_base_reso=1024,
        )

        self.assertEqual(plan.output_size, (400, 400))
        self.assertFalse(plan.needs_resize)

    def test_small_near_square_image_preserves_area_instead_of_snapping_to_512(self):
        plan = plan_arb_export(
            624,
            603,
            base_resos=[512, 768, 1024, 1280, 1536, 1792, 2048],
            min_reso=512,
            max_reso=3072,
            step=64,
            max_base_reso=2048,
            output_max_base_reso=1024,
        )

        self.assertEqual(plan.bucket_size, (512, 512))
        self.assertEqual(plan.crop_box, (10, 0, 603, 603))
        self.assertEqual(plan.output_size, (603, 603))
        self.assertFalse(plan.needs_resize)

    def test_oversized_image_downsamples_to_max_base_bucket(self):
        plan = plan_arb_export(
            2048,
            2048,
            base_resos=[512, 768, 1024, 1536],
            min_reso=512,
            max_reso=2048,
            step=64,
            output_max_base_reso=1024,
        )

        self.assertEqual(plan.bucket_size, (1600, 1600))
        self.assertEqual(plan.crop_box, (0, 0, 2048, 2048))
        self.assertEqual(plan.output_size, (1024, 1024))
        self.assertEqual(plan.action, "downsample")
        self.assertTrue(plan.needs_resize)


class DetailPreservingDownsampleTests(unittest.TestCase):
    def test_downsample_preserving_detail_returns_target_size(self):
        img = Image.new("RGB", (1800, 1200), (128, 64, 32))

        out = downsample_preserving_detail(img, (1536, 1024))

        self.assertEqual(out.size, (1536, 1024))


class RouteExportHelperTests(unittest.TestCase):
    def test_export_crop_uses_crop_only_for_near_bucket_image(self):
        class Req:
            export_strategy = "arb_crop"
            bucket_base_resos = [512, 768, 1024]
            bucket_min_base_reso = 0
            bucket_max_base_reso = 1024
            output_max_base_reso = 1024
            bucket_base_reso_steps = 256
            min_bucket_reso = 512
            max_bucket_reso = 2048
            step = 64

        img = Image.new("RGB", (1053, 1053), (255, 255, 255))

        out, action, bucket = _export_crop(img, 1024, 1024, Req())

        self.assertEqual(out.size, (1024, 1024))
        self.assertEqual(action, "crop")
        self.assertEqual(bucket, (1024, 1024))


class TextureBucketTests(unittest.TestCase):
    AREA_1024 = 1024 * 1024

    def test_texture_buckets_square_inclusive_ar_capped_and_sorted(self):
        buckets = texture_buckets(self.AREA_1024, step=64, max_ar=2.0)
        self.assertIn((1024, 1024), buckets)
        for w, h in buckets:
            self.assertLessEqual(max(w / h, h / w), 2.0 + 1e-9)
        # 与 get_standard_buckets 一致：按宽高比排序
        self.assertEqual(buckets, sorted(buckets, key=lambda b: b[0] / b[1]))

    def test_square_draw_snaps_to_1024_square(self):
        snap = snap_to_texture_bucket(0, 0, 1100, 1100, 4000, 4000,
                                      self.AREA_1024, step=64)
        self.assertIsNotNone(snap)
        _, _, w, h = snap
        self.assertEqual((w, h), (1024, 1024))

    def test_wide_draw_snaps_to_wide_bucket_fitting_inside_rect(self):
        snap = snap_to_texture_bucket(100, 100, 1400, 800, 4000, 4000,
                                      self.AREA_1024, step=64)
        self.assertIsNotNone(snap)
        _, _, w, h = snap
        self.assertGreater(w, h)        # 宽桶
        self.assertLessEqual(w, 1400)   # 塞进画框
        self.assertLessEqual(h, 800)

    def test_snap_centers_on_drawn_center_and_stays_in_bounds(self):
        snap = snap_to_texture_bucket(1450, 1450, 1100, 1100, 4000, 4000,
                                      self.AREA_1024, step=64)
        self.assertIsNotNone(snap)
        x, y, w, h = snap
        self.assertAlmostEqual(x + w / 2, 1450 + 1100 / 2, delta=1)
        self.assertAlmostEqual(y + h / 2, 1450 + 1100 / 2, delta=1)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 4000)
        self.assertLessEqual(y + h, 4000)

    def test_region_too_small_for_any_bucket_returns_none(self):
        # 700x700 容不下任何 ~1MP 桶
        self.assertIsNone(
            snap_to_texture_bucket(0, 0, 700, 700, 4000, 4000,
                                   self.AREA_1024, step=64)
        )

    def test_clamps_to_source_top_left_corner(self):
        snap = snap_to_texture_bucket(0, 0, 1200, 1200, 2000, 2000,
                                      self.AREA_1024, step=64)
        self.assertIsNotNone(snap)
        x, y, w, h = snap
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 2000)
        self.assertLessEqual(y + h, 2000)

    def test_source_smaller_than_smallest_bucket_returns_none(self):
        # 源图最小边 800 < 最小桶最短边(~704+) 时仍可能成桶；
        # 用 600x4000 这种极窄源图保证任何桶都塞不下
        self.assertIsNone(
            snap_to_texture_bucket(0, 0, 600, 4000, 600, 4000,
                                   self.AREA_1024, step=64)
        )


if __name__ == "__main__":
    unittest.main()
