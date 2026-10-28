import React, { useEffect, useRef } from 'react';

// A moving web: points drift across the page and link up with lines when they
// come near each other, and near the pointer. Fixed behind the whole page.
export const BackgroundFX: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    let width = 0;
    let height = 0;
    let frame = 0;
    const pointer = { x: -9999, y: -9999, tx: -9999, ty: -9999 };

    type Dot = { x: number; y: number; vx: number; vy: number; r: number };
    let dots: Dot[] = [];

    const LINK = 150;
    const POINTER_LINK = 190;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.round(Math.min(120, (width * height) / 12000));
      dots = Array.from({ length: count }, () => {
        const angle = Math.random() * Math.PI * 2;
        const speed = 0.2 + Math.random() * 0.25;
        return {
          x: Math.random() * width,
          y: Math.random() * height,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          r: Math.random() * 1.2 + 0.9
        };
      });
    };

    const onPointer = (e: PointerEvent) => {
      pointer.tx = e.clientX;
      pointer.ty = e.clientY;
    };
    const onLeave = () => {
      pointer.tx = -9999;
      pointer.ty = -9999;
    };

    const draw = () => {
      frame = requestAnimationFrame(draw);
      if (document.hidden) return;
      ctx.clearRect(0, 0, width, height);

      pointer.x += (pointer.tx - pointer.x) * 0.12;
      pointer.y += (pointer.ty - pointer.y) * 0.12;

      // A faint glow under the pointer.
      if (pointer.x > -1000) {
        const g = ctx.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 170);
        g.addColorStop(0, 'rgba(16,185,129,0.045)');
        g.addColorStop(1, 'rgba(16,185,129,0)');
        ctx.fillStyle = g;
        ctx.fillRect(pointer.x - 170, pointer.y - 170, 340, 340);
      }

      for (const d of dots) {
        d.x += d.vx;
        d.y += d.vy;
        // Bounce off the edges so the web stays on screen.
        if (d.x < 0 || d.x > width) d.vx *= -1;
        if (d.y < 0 || d.y > height) d.vy *= -1;
        d.x = Math.max(0, Math.min(width, d.x));
        d.y = Math.max(0, Math.min(height, d.y));
      }

      ctx.lineWidth = 1;
      for (let i = 0; i < dots.length; i++) {
        const a = dots[i];
        for (let j = i + 1; j < dots.length; j++) {
          const b = dots[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          if (Math.abs(dx) > LINK || Math.abs(dy) > LINK) continue;
          const dist = Math.hypot(dx, dy);
          if (dist < LINK) {
            ctx.strokeStyle = `rgba(52,211,153,${(1 - dist / LINK) * 0.22})`;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
        // Threads to the pointer.
        const pd = Math.hypot(a.x - pointer.x, a.y - pointer.y);
        if (pd < POINTER_LINK) {
          ctx.strokeStyle = `rgba(94,234,212,${(1 - pd / POINTER_LINK) * 0.28})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(pointer.x, pointer.y);
          ctx.stroke();
        }
      }

      ctx.fillStyle = 'rgba(110,231,183,0.7)';
      for (const d of dots) {
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    resize();
    frame = requestAnimationFrame(draw);
    window.addEventListener('resize', resize);
    window.addEventListener('pointermove', onPointer, { passive: true });
    document.addEventListener('pointerleave', onLeave);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onPointer);
      document.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  return (
    <div className="pointer-events-none fixed inset-0 z-0" aria-hidden="true">
      <div className="absolute left-[-10%] top-[-15%] h-[55vh] w-[55vw] rounded-full bg-emerald-600/[0.08] blur-[120px]" />
      <div className="absolute right-[-10%] top-[25%] h-[45vh] w-[40vw] rounded-full bg-teal-500/[0.05] blur-[120px]" />
      {/* Kept faint on purpose: the web should only just be visible behind the content. */}
      <canvas ref={canvasRef} className="absolute inset-0 opacity-20" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,rgba(10,12,11,0.7)_100%)]" />
    </div>
  );
};
