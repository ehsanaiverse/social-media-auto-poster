import { Canvas } from '@react-three/fiber'
import { OrbitControls, Float, Box, Text, Grid, Points } from '@react-three/drei'
import { memo, useMemo, Suspense } from 'react'

const FloatingParticles = memo(function FloatingParticles() {
  const positions = useMemo(() => {
    const pos = new Float32Array(50 * 3)
    for (let i = 0; i < 50; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 30
      pos[i * 3 + 1] = (Math.random() - 0.5) * 30
      pos[i * 3 + 2] = (Math.random() - 0.5) * 30
    }
    return pos
  }, [])

  return (
    <Points positions={positions}>
      <pointsMaterial size={0.05} sizeAttenuation transparent opacity={0.6} color="#818cf8" />
    </Points>
  )
})

const PlatformIcon = memo(function PlatformIcon({ position, color, label }: { position: [number, number, number]; color: string; label: string }) {
  return (
    <Float speed={1.5} rotationIntensity={0.5} floatIntensity={0.5}>
      <group position={position}>
        <Box args={[0.8, 0.8, 0.8]}>
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.4} roughness={0.3} />
        </Box>
        <Suspense fallback={null}>
          <Text
            position={[0, -1.2, 0]}
            fontSize={0.4}
            color="#ffffff"
            anchorX="center"
            anchorY="middle"
          >
            {label}
          </Text>
        </Suspense>
      </group>
    </Float>
  )
})

function Scene3D() {
  return (
    <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
      <Canvas
        camera={{ position: [0, 0, 12], fov: 45 }}
        style={{ width: '100%', height: '100%', opacity: 0.45 }}
        gl={{ alpha: true, antialias: true }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.7} />
          <pointLight position={[10, 10, 10]} intensity={1.2} color="#818cf8" />
          <pointLight position={[-10, -10, -10]} intensity={0.8} color="#f472b6" />

          <FloatingParticles />

          <PlatformIcon position={[-5, 2, 0]} color="#0ea5e9" label="LinkedIn" />
          <PlatformIcon position={[5, 2, 0]} color="#6366f1" label="AutoPoster" />
          <PlatformIcon position={[0, -3, 0]} color="#ec4899" label="AI Engine" />

          <Grid
            args={[40, 40]}
            position={[0, -5, 0]}
            cellColor="#818cf8"
            cellThickness={0.1}
            sectionColor="#4f46e5"
            sectionThickness={1}
            fadeDistance={30}
            fadeStrength={1.5}
          />

          <OrbitControls
            enableZoom={false}
            enablePan={false}
            autoRotate
            autoRotateSpeed={0.5}
            dampingFactor={0.05}
          />
        </Suspense>
      </Canvas>
    </div>
  )
}

export default Scene3D