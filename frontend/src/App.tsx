import React from 'react';
import { MotionConfig } from 'framer-motion';
import { ApiProvider } from './context/ApiContext';
import { BackgroundFX } from './components/BackgroundFX';
import { Navbar } from './components/Navbar';
import { HeroSection } from './components/HeroSection';
import { IntakeStudio } from './components/IntakeStudio';
import { ScanQueueStation } from './components/ScanQueueStation';
import { RunnerConsole } from './components/RunnerConsole';
import { WorkflowGraph } from './components/WorkflowGraph';
import { JobsLedger } from './components/JobsLedger';
import { LinkedInSentinel } from './components/LinkedInSentinel';
import { CtaSection } from './components/CtaSection';
import { Footer } from './components/Footer';
import { JobDetailsModal } from './components/JobDetailsModal';
import { ResolveModal } from './components/ResolveModal';
import { ToastContainer } from './components/ToastContainer';

export const App: React.FC = () => (
  // The animations are short fades and slides, so they play even when the OS
  // asks for reduced motion (the developer's choice, 2026-10-28).
  <MotionConfig reducedMotion="never">
    <ApiProvider>
      <a
        href="#intake"
        className="sr-only z-[90] rounded-lg bg-emerald-400 px-4 py-2 font-medium text-[#04110B] focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to content
      </a>
      <BackgroundFX />
      <div className="noise-overlay" aria-hidden="true" />
      <div className="relative z-10 flex min-h-screen flex-col font-sans text-slate-100">
        <Navbar />
        <main className="flex flex-1 flex-col">
          <HeroSection />
          <IntakeStudio />
          <ScanQueueStation />
          <RunnerConsole />
          <WorkflowGraph />
          <JobsLedger />
          <LinkedInSentinel />
          <CtaSection />
        </main>
        <Footer />
        <JobDetailsModal />
        <ResolveModal />
        <ToastContainer />
      </div>
    </ApiProvider>
  </MotionConfig>
);

export default App;
