import { useMemo, useState } from "react";
import type { Tunable } from "../lib/types";

type Value = number | boolean;

/**
 * Every tunable, built from the schema the server sends rather than from a
 * hand-written list — so one added in Python appears here with no front-end
 * change.
 *
 * Not all of them are sliders. Five are `kind: "bool"` — the ablations, which
 * switch the risk field, multi-modal prediction or the RSS supervisor off so
 * you can watch the stack degrade. Treating those as numbers is what broke
 * the first build: `values` carries a real JSON boolean, and `toFixed` is not
 * a method on one.
 */
export default function Panel({
  tunables, values, onSet, onReset, onClose,
}: {
  tunables: Tunable[];
  values: Record<string, Value>;
  onSet: (key: string, value: number) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const [local, setLocal] = useState<Record<string, Value>>(values ?? {});

  const sections = useMemo(() => {
    const by = new Map<string, Tunable[]>();
    for (const t of tunables ?? []) {
      const list = by.get(t.section) ?? [];
      list.push(t);
      by.set(t.section, list);
    }
    return [...by.entries()];
  }, [tunables]);

  const set = (key: string, v: Value) => {
    setLocal((p) => ({ ...p, [key]: v }));
    onSet(key, typeof v === "boolean" ? (v ? 1 : 0) : v);
  };

  return (
    <div className="sheet" role="dialog" aria-label="Thresholds">
      <header className="sheet__bar">
        <h2>Thresholds</h2>
        <span className="sheet__count">{(tunables ?? []).length} adjustable mid-run</span>
        <button className="key btn" onClick={onReset}>Reset</button>
        <button className="key btn" onClick={onClose}>Close</button>
      </header>

      <div className="sheet__body">
        {sections.map(([section, items]) => (
          <section key={section} className="group" data-section={section}>
            <h3 className="group__name">{section}</h3>

            {items.map((t) => {
              const raw = local[t.key];

              if (t.kind === "bool") {
                const checked = raw === true || raw === 1;
                return (
                  <label key={t.key} className="tune tune--bool">
                    <input type="checkbox" checked={checked}
                           onChange={(e) => set(t.key, e.target.checked)} />
                    <span className="tune__label">{t.label}</span>
                    <span className={`tune__flag ${checked ? "" : "is-off"}`}>
                      {checked ? "on" : "off"}
                    </span>
                    {t.help && <span className="tune__help">{t.help}</span>}
                  </label>
                );
              }

              const v = typeof raw === "number" && isFinite(raw) ? raw : t.lo;
              return (
                <label key={t.key} className="tune">
                  <span className="tune__label">{t.label}</span>
                  <span className="tune__value">{v.toFixed(2)}</span>
                  <input type="range" min={t.lo} max={t.hi} step={t.step} value={v}
                         onChange={(e) => set(t.key, Number(e.target.value))} />
                  {t.help && <span className="tune__help">{t.help}</span>}
                </label>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}
