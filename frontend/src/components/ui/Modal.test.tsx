// Regression: typing in a dialog's field must not send focus back to the dialog after each key.
import React, { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

// Like ResolveModal and ScheduleModal: a new onClose function on every render, and a text field inside.
const Dialog: React.FC<{ onClosed: () => void }> = ({ onClosed }) => {
  const [text, setText] = useState('');
  return (
    <Modal open onClose={() => onClosed()} title="A dialog">
      <label htmlFor="field">Field</label>
      <input id="field" value={text} onChange={(e) => setText(e.target.value)} />
    </Modal>
  );
};

describe('Modal', () => {
  it('keeps focus in the field while typing, even though its parent re-renders on every key', async () => {
    const u = userEvent.setup();
    render(<Dialog onClosed={() => {}} />);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await u.type(screen.getByLabelText('Field'), 'publish to LinkedIn');
    expect(screen.getByLabelText('Field')).toHaveValue('publish to LinkedIn');
    expect(screen.getByLabelText('Field')).toHaveFocus();
  });

  it('still closes with Escape, using the latest onClose', async () => {
    const u = userEvent.setup();
    const onClosed = vi.fn();
    render(<Dialog onClosed={onClosed} />);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await u.type(screen.getByLabelText('Field'), 'abc');
    await u.keyboard('{Escape}');
    expect(onClosed).toHaveBeenCalledTimes(1);
  });
});
