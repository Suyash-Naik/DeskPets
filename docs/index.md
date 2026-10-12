# Desk Pets

Desk Pets is a small desktop application I created for my own use. While running a
Docker-based virtual machine on my modest home PC, I found it difficult to keep a clear sense
of how much of the system's resources I was using. That became the original idea: a simple GUI
that gives me immediate, visual feedback about the state of my computer without making me
open a monitoring dashboard.

I also wanted the application to feel personal. I adopted Layla and Chico last year, so I
based the animated pets on them. This lets me keep a little version of my kittens nearby while
I work. Aren't they the cutest?

![Layla and Chico reacting to system activity](pets_showcase.gif)

## How does it work?

Desk Pets runs as a frameless, transparent, always-on-top window on Windows. A background
worker periodically reads system telemetry such as CPU, RAM, GPU, temperature, swap, disk,
and user inactivity. The UI stays responsive because this monitoring work runs separately
from the main window.

A state machine translates those readings into animations. The pets may remain attentive
while the system is calm, play while it is moderately busy, jump during heavy load, become
grumpy when the system is under stress, or fall asleep when the computer has been idle. The
state changes use hysteresis and minimum dwell times so the animation does not flicker when a
reading moves back and forth around a threshold.

Hovering over the pets opens a small stats card with the latest readings. The system tray menu
provides controls for visibility, always-on-top behavior, click-through mode, position reset,
and manually previewing each animation.

## What is it supposed to do?

The goal is to make system activity understandable at a glance. Desk Pets should answer a
simple question without interrupting my work: *Is my computer calm, busy, struggling, or
waiting for me?*

It is intended to:

- provide immediate visual feedback about system load;
- make it easier to notice sustained CPU, RAM, GPU, temperature, or swap pressure;
- remain lightweight and unobtrusive during normal work;
- show detailed telemetry only when I ask for it by hovering over the pets; and
- make routine system monitoring feel more personal and enjoyable.

Desk Pets is a companion rather than a diagnostic or benchmarking tool. Its animations
summarize the current state, while the stats card provides the underlying numbers when I need
them.

## How was it generated?

The artwork was produced as a staged, AI-assisted animation pipeline using photographs and
visual references of Layla and Chico. Each behavior was created as a separate animation
sequence while preserving their recognizable features: Layla is the slimmer black cat with
upright ears, and Chico is the rounder charcoal cat with folded ears.

Generated animation strips are cleaned, separated into individual frames, registered on a
shared canvas, and reviewed for alignment and motion. Deterministic scripts then package the
approved frames into a sprite atlas. A JSON manifest records every frame rectangle, animation
name, loop setting, and duration, so the application reads the real animation metadata instead
of relying on hard-coded frame counts or timing.

The application itself is written in Python with PyQt6. It was developed iteratively alongside
the artwork, with separate components for telemetry, animation playback, state selection,
Windows integration, the pet window, the stats card, and the system tray.

More detail about the art process is available in the
[animation pipeline guide](codex-animation-pipeline-guide.md).

## What is the plan?

The immediate focus is to stabilize and refine the Windows version:

- tune telemetry thresholds using real workloads on my computer;
- improve animations whose motion or timing still needs revision;
- validate long-running behavior, shutdown, sleep, resume, and multi-monitor handling;
- improve hardware coverage, including non-NVIDIA GPUs and a reliable Windows CPU-temperature
  source;
- package the application for convenient installation and startup; and
- continue improving the pets, interactions, and stats card without making the application
  heavy or distracting.

A broader goal of this project is to help me learn how desktop systems can be integrated with
agentic AI. I would like to add a small chat window that allows Layla and
Chico to interact with large language models. 

Instead of being only visual indicators, the pets could become a friendly interface for asking questions, understanding what is happening on the computer, and eventually helping with useful tasks.

This would also give me a practical way to explore the parts of agentic software that interest
me most: connecting an LLM to live application context, giving it carefully scoped tools, and
turning its responses into actions that remain understandable and under my control. The exact
features are still open, but I would like the AI integration to grow naturally from the pets
rather than feel like a separate assistant placed on top of them.

Windows is the current target. Linux and macOS support may follow later, but their desktop and
window-management behavior requires dedicated platform work rather than a simple packaging
change.

The project will continue to evolve around its original purpose: giving me a quick sense of my
computer's health while keeping Layla and Chico close during the day.
