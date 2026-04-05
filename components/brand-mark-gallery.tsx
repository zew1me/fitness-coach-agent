import Image from "next/image";
import React from "react";
import type { JSX } from "react";

const iconOptions = [
  {
    file: "/brand/peak-mark-ridge.svg",
    name: "Ridge",
    note: "Closest to your sketch. Broad ridge line, one orange snow notch, no sport-specific cues."
  },
  {
    file: "/brand/peak-mark-summit.svg",
    name: "Summit",
    note: "More symmetrical and badge-friendly. Reads like a durable app icon at smaller sizes."
  },
  {
    file: "/brand/peak-mark-horizon.svg",
    name: "Horizon",
    note: "Lowest visual noise. More landscape than peak badge, and now the cleanest no-accent direction."
  }
] as const;

export function BrandMarkGallery(): JSX.Element {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Brand Mark Directions</h2>
          <p>Simple mountain symbols with a small orange snow flash. No letters, no single-sport cues.</p>
        </div>
      </div>
      <div className="icon-gallery">
        {iconOptions.map((option) => (
          <article className="icon-card" key={option.file}>
            <div className="icon-frame">
              <Image
                alt={`${option.name} brand mark option`}
                height={144}
                priority={option.name === "Ridge"}
                src={option.file}
                width={208}
              />
            </div>
            <div>
              <strong>{option.name}</strong>
              <p>{option.note}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
