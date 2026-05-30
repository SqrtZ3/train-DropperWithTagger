import asyncio
import contextlib
import io
import os
import tempfile
import unittest

from PIL import Image, ImageDraw

from webapp.buckets import (
    choose_arb_bucket,
    downsample_preserving_detail,
    plan_arb_export,
    snap_to_texture_bucket,
    texture_buckets,
)
from webapp.routes_crop import _export_crop, _export_texture, process_batch
from webapp.schemas import BatchProcessRequest
from webapp.session_manager import session


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

    def test_downsample_is_gamma_correct(self):
        # 黑白棋盘缩到 1px：感知上 50% 白覆盖 → 线性 0.5 → sRGB ~188。
        # 在 sRGB(gamma)编码值上直接平均会得到 ~128（偏暗 = 「画面损伤」）。
        chk = Image.new("RGB", (256, 256))
        px = chk.load()
        for y in range(256):
            for x in range(256):
                v = 255 if (x + y) % 2 == 0 else 0
                px[x, y] = (v, v, v)
        out = downsample_preserving_detail(chk, (1, 1)).getpixel((0, 0))
        # gamma-correct 应落在 ~188 附近，远离 naive 的 ~128
        self.assertGreater(out[0], 170)


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


def _spotted(w, h, n=60):
    """带高频色块的图：重采样会改像素，故可用于验证『零重采样』。"""
    img = Image.new("RGB", (w, h), (30, 30, 30))
    d = ImageDraw.Draw(img)
    for i in range(n):
        x0 = (i * 53) % max(1, w - 50)
        y0 = (i * 31) % max(1, h - 50)
        d.rectangle([x0, y0, x0 + 45, y0 + 45],
                    fill=((i * 7) % 256, (i * 15) % 256, (i * 23) % 256))
    return img


class RouteTextureExportTests(unittest.TestCase):
    def test_export_texture_native_and_pixel_identical(self):
        img = _spotted(1600, 1400)
        item = {"x": 200, "y": 150, "w": 1300, "h": 1300}
        out = _export_texture(img, item, 1024 * 1024, 64, 2.0)
        self.assertIsNotNone(out)
        cropped, (bw, bh) = out
        self.assertEqual(cropped.size, (bw, bh))
        # 与独立 snap+crop 的结果逐像素一致 → 证明零重采样
        nx, ny, _, _ = snap_to_texture_bucket(
            200, 150, 1300, 1300, 1600, 1400, 1024 * 1024, 64, 2.0)
        ref = img.crop((nx, ny, nx + bw, ny + bh))
        self.assertEqual(cropped.tobytes(), ref.tobytes())  # 逐像素一致

    def test_export_texture_none_when_region_too_small(self):
        img = _spotted(4000, 4000)
        item = {"x": 0, "y": 0, "w": 700, "h": 700}
        self.assertIsNone(_export_texture(img, item, 1024 * 1024, 64, 2.0))


class ProcessBatchSplitTests(unittest.TestCase):
    def test_full_texture_split_and_honest_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "in")
            out = os.path.join(tmp, "out")
            os.makedirs(inp)
            a = os.path.join(inp, "a.png"); _spotted(1100, 1100).save(a, "PNG")
            b = os.path.join(inp, "b.png"); _spotted(1080, 1080).save(b, "PNG")
            big = os.path.join(inp, "big.png"); _spotted(3000, 2200).save(big, "PNG")

            # 直接配置全局 session，绕开 initialize（不污染 .drop_cache）
            session.queue = [
                {"filepath": a, "filename": "a.png", "x": 0, "y": 0, "w": 1100, "h": 1100, "crop_id": 0, "kind": "full"},
                {"filepath": b, "filename": "b.png", "x": 0, "y": 0, "w": 1080, "h": 1080, "crop_id": 0, "kind": "full"},
                {"filepath": big, "filename": "big.png", "x": 400, "y": 300, "w": 1400, "h": 1400, "crop_id": 0, "kind": "texture"},
            ]
            session.op_history = []
            session.output_folder = out
            session._state_file = None        # _save_state() 变 noop
            session.auto_tag_enabled = False
            session.set_target(target_mp=1.0, step=64)

            # 捕获 stdout：进度 print 含 emoji，GBK 控制台会崩；测试不该依赖控制台编码
            with contextlib.redirect_stdout(io.StringIO()):
                res = asyncio.run(process_batch(
                    BatchProcessRequest(export_strategy="resize", min_bucket_size=1,
                                        target_mp=1.0, step=64)))

            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["processed"], 2)
            self.assertEqual(res["texture_processed"], 1)

            # 普通框：真实落盘 == 报告桶；resize 下都是 1024²
            full_files = [f for f in os.listdir(out) if f.endswith(".png")]
            self.assertEqual(len(full_files), 2)
            for f in full_files:
                with Image.open(os.path.join(out, f)) as im:
                    self.assertEqual(im.size, (1024, 1024))
            self.assertEqual(res["buckets"], [{"w": 1024, "h": 1024, "count": 2}])

            # 纹理框：原生尺寸、进 texture 子目录、与报告一致
            tex_dir = os.path.join(out, "texture")
            tex_files = [f for f in os.listdir(tex_dir) if f.endswith(".png")]
            self.assertEqual(len(tex_files), 1)
            tw, th = res["texture_sizes"][0]["w"], res["texture_sizes"][0]["h"]
            with Image.open(os.path.join(tex_dir, tex_files[0])) as im:
                self.assertEqual(im.size, (tw, th))

    def test_degenerate_box_is_skipped_not_black_64(self):
        """退化框（宽/高 ≤0，如坐标格式错配产生的负宽）必须被跳过，
        而不是导出 64×64 纯黑图。"""
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "in"); out = os.path.join(tmp, "out")
            os.makedirs(inp)
            good = os.path.join(inp, "good.png"); _spotted(1200, 1200).save(good, "PNG")
            bad = os.path.join(inp, "bad.png"); _spotted(1200, 1200).save(bad, "PNG")

            session.queue = [
                {"filepath": good, "filename": "good.png", "x": 50, "y": 50, "w": 1000, "h": 1000, "crop_id": 0, "kind": "full"},
                {"filepath": bad, "filename": "bad.png", "x": 800, "y": 50, "w": -200, "h": 600, "crop_id": 0, "kind": "full"},
            ]
            session.op_history = []
            session.output_folder = out
            session._state_file = None
            session.auto_tag_enabled = False
            session.set_target(target_mp=1.0, step=64)

            with contextlib.redirect_stdout(io.StringIO()):
                res = asyncio.run(process_batch(
                    BatchProcessRequest(export_strategy="resize", min_bucket_size=1,
                                        target_mp=1.0, step=64)))

            self.assertEqual(res["processed"], 1)
            self.assertEqual(res["degenerate_skipped"], 1)
            files = [f for f in os.listdir(out) if f.endswith(".png")]
            self.assertEqual(len(files), 1)
            for f in files:
                with Image.open(os.path.join(out, f)) as im:
                    self.assertNotEqual(im.size, (64, 64))


if __name__ == "__main__":
    unittest.main()
