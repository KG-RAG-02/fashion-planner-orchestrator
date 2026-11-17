"""Procedural red satin drape on a white studio sweep (original artwork for the hero).

python3 silk.py OUT.jpg [--w 2400] [--h 1350] [--seed 7]
"""
import argparse

import numpy as np
from PIL import Image, ImageFilter


def srgb_to_lin(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--w", type=int, default=2400)
    ap.add_argument("--h", type=int, default=1350)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    W, H = args.w, args.h
    aspect = W / H
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    x = xs / H  # 0..aspect
    y = ys / H  # 0..1

    # --- fold field: folds radiate from a gather point off the top-right edge ---
    gx, gy = aspect + 0.10, -0.22
    dx, dy = x - gx, y - gy
    r = np.hypot(dx, dy)
    ang = np.arctan2(dy, dx)
    h = np.zeros_like(x)
    for k in range(7):
        freq = rng.uniform(5.5, 15.0) * (1 + 0.6 * k / 6)
        amp = rng.uniform(0.35, 1.0) / (1 + 0.55 * k)
        phase = rng.uniform(0, 2 * np.pi)
        drift = rng.uniform(-1.2, 1.2)
        # folds open up (lower frequency) as they fall away from the gather point
        wave = freq * ang * (2.2 / (0.35 + r)) + phase + drift * r
        h += amp * (0.55 * np.sin(wave) + 0.45 * (1 - 2 * np.abs(np.sin(0.5 * wave))))
    h *= 0.018
    # broad billow across the cloth
    h += 0.05 * np.sin(1.6 * r + 0.8) * np.cos(0.9 * ang * 3)

    # --- cloth region: a drape falling from the right edge and pooling along the bottom ---
    edge = 0.60 + 0.30 * smoothstep(aspect * 0.95, aspect * 0.25, x) ** 1.2 - 0.30 * smoothstep(aspect * 0.55, aspect * 1.0, x)
    edge += 0.012 * np.sin(x * 7.0 + 0.6)
    edge += h * 2.6
    inside = smoothstep(-0.0025, 0.0025, y - edge)
    rim = np.exp(-np.clip(y - edge, 0, None) * 140.0) * inside  # hem rolls toward the light

    # --- normals and satin lighting ---
    hy, hx = np.gradient(h * H * 0.9)
    nx, ny, nz = -hx, -hy, np.ones_like(h)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    L = np.array([-0.45, -0.62, 0.64]); L /= np.linalg.norm(L)
    V = np.array([0.0, 0.0, 1.0])
    Hv = (L + V); Hv /= np.linalg.norm(Hv)
    ndl = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
    ndh = np.clip(nx * Hv[0] + ny * Hv[1] + nz * Hv[2], 0, 1)
    spec = ndh ** 60 * 0.75 + ndh ** 10 * 0.10  # tight satin sheen + faint bloom
    cavity = smoothstep(-0.03, 0.03, h)  # valleys darker

    red = srgb_to_lin([229, 0, 16])
    deep = srgb_to_lin([70, 0, 8])
    sheen = srgb_to_lin([255, 196, 200])
    base = deep[None, None, :] * (1 - ndl[..., None]) + red[None, None, :] * ndl[..., None]
    cloth = base * (0.45 + 0.6 * cavity[..., None]) + sheen[None, None, :] * spec[..., None] * 0.55
    cloth = cloth * (1 - 0.35 * rim[..., None]) + sheen[None, None, :] * 0.35 * rim[..., None]

    # --- studio sweep background ---
    top = srgb_to_lin([248, 248, 247]); bottom = srgb_to_lin([226, 226, 225])
    t = smoothstep(0.0, 1.0, y)[..., None]
    bg = top * (1 - t) + bottom * t
    # soft red bounce light on the paper near the cloth
    bounce = np.exp(-np.clip(edge - y, 0, None) * 9.0) * (1 - inside)
    bg = bg * (1 - 0.10 * bounce[..., None]) + red * 0.10 * bounce[..., None]
    # contact shadow just above the cloth edge
    ambient = np.exp(-np.clip(edge - y, 0, None) * 14.0) * (1 - inside)
    bg *= (1 - 0.06 * ambient[..., None])
    vign = 1 - 0.10 * ((x / aspect - 0.5) ** 2 + (y - 0.45) ** 2)
    bg *= vign[..., None]

    img = bg * (1 - inside[..., None]) + cloth * inside[..., None]
    out = (lin_to_srgb(img) * 255).astype(np.float64)
    out += rng.normal(0, 1.6, out.shape)  # fine film grain
    im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    im.save(args.out, "JPEG", quality=84, optimize=True, progressive=True)
    print("saved", args.out, im.size)


if __name__ == "__main__":
    main()
