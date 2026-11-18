import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';

interface NetworkGlobeProps {
  className?: string;
  isProcessing?: boolean;
}

// Each glowing node travels on its own tilted orbit around the globe, trailed
// by a fading tail, and sends small light packets into the core. Rendering
// pauses while the globe is off screen or the tab is hidden.
const ORBITS = [
  { radius: 7.2, tilt: 0.35, spin: 0.0, speed: 0.42, color: 0x34d399 },
  { radius: 7.6, tilt: -0.6, spin: 1.1, speed: -0.33, color: 0x2dd4bf },
  { radius: 8.2, tilt: 1.15, spin: 2.2, speed: 0.27, color: 0x6ee7b7 },
  { radius: 6.8, tilt: -1.2, spin: 0.6, speed: -0.5, color: 0xa78bfa },
  { radius: 7.9, tilt: 0.9, spin: 3.0, speed: 0.36, color: 0xfbbf24 }
] as const;

const TRAIL = 26;

const NetworkGlobe3D: React.FC<NetworkGlobeProps> = ({ className = '', isProcessing = false }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const isProcessingRef = useRef(isProcessing);
  isProcessingRef.current = isProcessing;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
    } catch {
      return; // No WebGL: the panel keeps its CSS background.
    }

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    camera.position.set(0, 2, 25);
    camera.lookAt(0, 0, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.setAttribute('aria-hidden', 'true');
    renderer.domElement.style.display = 'block';
    container.appendChild(renderer.domElement);

    const disposables: { dispose: () => void }[] = [];
    const track = <T extends { dispose: () => void }>(x: T) => (disposables.push(x), x);

    const world = new THREE.Group();
    scene.add(world);

    // Globe: a faint wireframe plus latitude rings.
    const globe = new THREE.Mesh(
      track(new THREE.IcosahedronGeometry(5.2, 3)),
      track(new THREE.MeshBasicMaterial({ color: 0x1f3a30, wireframe: true, transparent: true, opacity: 0.35 }))
    );
    world.add(globe);
    const glow = new THREE.Mesh(
      track(new THREE.SphereGeometry(5.4, 48, 48)),
      track(new THREE.MeshBasicMaterial({ color: 0x10b981, transparent: true, opacity: 0.05, side: THREE.BackSide }))
    );
    world.add(glow);

    const coreMat = track(new THREE.MeshBasicMaterial({ color: 0x34d399, wireframe: true, transparent: true, opacity: 0.9 }));
    const core = new THREE.Mesh(track(new THREE.OctahedronGeometry(1.6, 0)), coreMat);
    world.add(core);

    const nodeGeo = track(new THREE.SphereGeometry(0.42, 20, 20));
    const haloGeo = track(new THREE.SphereGeometry(0.85, 20, 20));
    const packetGeo = track(new THREE.SphereGeometry(0.14, 10, 10));

    const orbits = ORBITS.map((o) => {
      const pivot = new THREE.Group();
      pivot.rotation.set(o.tilt, o.spin, 0);
      world.add(pivot);

      // The orbit path
      const curve = new THREE.EllipseCurve(0, 0, o.radius, o.radius, 0, Math.PI * 2);
      const pts = curve.getPoints(128).map((p) => new THREE.Vector3(p.x, 0, p.y));
      pivot.add(
        new THREE.LineLoop(
          track(new THREE.BufferGeometry().setFromPoints(pts)),
          track(new THREE.LineBasicMaterial({ color: o.color, transparent: true, opacity: 0.16 }))
        )
      );

      const node = new THREE.Mesh(nodeGeo, track(new THREE.MeshBasicMaterial({ color: o.color })));
      const halo = new THREE.Mesh(haloGeo, track(new THREE.MeshBasicMaterial({ color: o.color, transparent: true, opacity: 0.18 })));
      node.add(halo);
      pivot.add(node);

      // Fading tail behind the node
      const trailPositions = new Float32Array(TRAIL * 3);
      const trailColors = new Float32Array(TRAIL * 3);
      const c = new THREE.Color(o.color);
      for (let i = 0; i < TRAIL; i++) {
        const k = 1 - i / TRAIL;
        trailColors.set([c.r * k, c.g * k, c.b * k], i * 3);
      }
      const trailGeo = track(new THREE.BufferGeometry());
      trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3));
      trailGeo.setAttribute('color', new THREE.BufferAttribute(trailColors, 3));
      pivot.add(new THREE.Line(trailGeo, track(new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9 }))));

      // A packet that flies from the node into the core
      const packet = new THREE.Mesh(packetGeo, track(new THREE.MeshBasicMaterial({ color: 0xecfdf5, transparent: true })));
      pivot.add(packet);

      return { ...o, pivot, node, halo, trailGeo, trailPositions, packet, angle: o.spin * 2, packetT: Math.random() };
    });

    // Star dust around everything
    const count = 260;
    const dust = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = 10 + Math.random() * 6;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      dust.set([r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi) * 0.5, r * Math.sin(phi) * Math.sin(theta)], i * 3);
    }
    const dustGeo = track(new THREE.BufferGeometry());
    dustGeo.setAttribute('position', new THREE.BufferAttribute(dust, 3));
    const dustPoints = new THREE.Points(dustGeo, track(new THREE.PointsMaterial({ color: 0x6ee7b7, size: 0.08, transparent: true, opacity: 0.55 })));
    scene.add(dustPoints);

    let pointerX = 0;
    let pointerY = 0;
    let tiltX = 0;
    let tiltY = 0;
    const onPointer = (e: PointerEvent) => {
      const r = container.getBoundingClientRect();
      pointerX = ((e.clientX - r.left) / r.width - 0.5) * 2;
      pointerY = ((e.clientY - r.top) / r.height - 0.5) * 2;
    };
    const onLeave = () => {
      pointerX = 0;
      pointerY = 0;
    };
    container.addEventListener('pointermove', onPointer);
    container.addEventListener('pointerleave', onLeave);

    let running = false;
    const resize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = '100%';
      if (!running) renderer.render(scene, camera);
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);

    const timer = new THREE.Timer();
    let elapsed = 0;
    let frame = 0;
    let visible = true;
    const pos = new THREE.Vector3();

    const tick = () => {
      frame = requestAnimationFrame(tick);
      timer.update();
      const busy = isProcessingRef.current;
      const dt = Math.min(timer.getDelta(), 0.05) * (busy ? 2.4 : 1);
      elapsed += dt;

      tiltX += (pointerX * 0.35 - tiltX) * 0.05;
      tiltY += (pointerY * 0.35 - tiltY) * 0.05;
      world.rotation.y += 0.12 * dt;
      world.rotation.x = tiltY * 0.5;
      world.rotation.z = -tiltX * 0.25;
      globe.rotation.y -= 0.05 * dt;

      core.rotation.x = elapsed * 0.7;
      core.rotation.y = elapsed * 1.0;
      core.scale.setScalar(1 + Math.sin(elapsed * 3) * 0.1);
      coreMat.color.setHex(busy ? 0x5eead4 : 0x34d399);

      for (const o of orbits) {
        o.angle += o.speed * dt;
        o.node.position.set(Math.cos(o.angle) * o.radius, 0, Math.sin(o.angle) * o.radius);
        o.halo.scale.setScalar(1 + Math.sin(elapsed * 2.5 + o.spin) * 0.25);

        for (let i = 0; i < TRAIL; i++) {
          const a = o.angle - Math.sign(o.speed) * i * 0.035;
          o.trailPositions.set([Math.cos(a) * o.radius, 0, Math.sin(a) * o.radius], i * 3);
        }
        o.trailGeo.attributes.position.needsUpdate = true;

        o.packetT += dt * (busy ? 0.9 : 0.45);
        if (o.packetT > 1) o.packetT = 0;
        pos.copy(o.node.position).multiplyScalar(1 - o.packetT);
        o.packet.position.copy(pos);
        (o.packet.material as THREE.MeshBasicMaterial).opacity = Math.sin(o.packetT * Math.PI);
      }

      dustPoints.rotation.y = -elapsed * 0.02;
      renderer.render(scene, camera);
    };

    const update = () => {
      const shouldRun = visible && !document.hidden;
      if (shouldRun && !running) {
        running = true;
        timer.reset();
        frame = requestAnimationFrame(tick);
      } else if (!shouldRun && running) {
        running = false;
        cancelAnimationFrame(frame);
      }
    };

    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    });
    io.observe(container);
    document.addEventListener('visibilitychange', update);
    resize();
    update();

    return () => {
      running = false;
      cancelAnimationFrame(frame);
      io.disconnect();
      resizeObserver.disconnect();
      document.removeEventListener('visibilitychange', update);
      container.removeEventListener('pointermove', onPointer);
      container.removeEventListener('pointerleave', onLeave);
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return <div ref={containerRef} className={`relative h-full w-full ${className}`} />;
};

export default NetworkGlobe3D;
