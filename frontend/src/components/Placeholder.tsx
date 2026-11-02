export default function Placeholder({ title, phase }: { title: string; phase: number }) {
  return (
    <section>
      <h2>{title}</h2>
      <p className="muted">Implemented in Phase {phase}.</p>
    </section>
  );
}
