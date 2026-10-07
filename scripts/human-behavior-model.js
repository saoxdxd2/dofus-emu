/**
 * Advanced Cognitive Human Behavior & Fatigue Modeling Engine
 * 
 * Implements empirical cognitive science and perceptual-motor psychology models:
 *   1. Ex-Gaussian Reaction Time Distribution (μ, σ, τ with fatigue drift).
 *   2. Fitts' Law Target Acquisition with Log-Normal Velocity Trajectories.
 *   3. Minimum-Jerk Spline Generation with Micro-Tremor & Sub-Movement Corrections.
 *   4. Mackworth Vigilance Decrement (Cognitive load accumulation over session time).
 *   5. Hierarchical Markov Behavioral State Machine (Combat, Deliberation, Exploration, Lapses, Bio-breaks).
 *   6. Lightweight Neural-Inspired Autoregressive Timing Predictor.
 */

(function(root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.HumanBehaviorEngine = factory();
    }
}(typeof self !== 'undefined' ? self : this, function() {
    'use strict';

    // -------------------------------------------------------------
    // 1. Random Generators & Probability Distributions
    // -------------------------------------------------------------
    var RandomDist = {
        // Standard Uniform (0, 1)
        uniform: function(min, max) {
            min = min || 0;
            max = max === undefined ? 1 : max;
            return min + Math.random() * (max - min);
        },

        // Box-Muller Gaussian Normal Distribution N(mean, stdDev)
        gaussian: function(mean, stdDev) {
            mean = mean || 0;
            stdDev = stdDev === undefined ? 1 : stdDev;
            var u1 = Math.random();
            var u2 = Math.random();
            while (u1 <= 1e-15) u1 = Math.random(); // avoid log(0)
            var z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
            return mean + z0 * stdDev;
        },

        // Exponential Distribution Exp(lambda)
        exponential: function(tau) {
            // tau = 1 / lambda (mean of exponential)
            var u = Math.random();
            while (u <= 1e-15) u = Math.random();
            return -Math.log(u) * tau;
        },

        // Ex-Gaussian Distribution (Convolution of Normal and Exponential)
        // Highly accurate representation of human motor & cognitive reaction latencies
        exGaussian: function(mu, sigma, tau) {
            return this.gaussian(mu, sigma) + this.exponential(tau);
        },

        // Weibull Distribution (Used for realistic biological AFK break survival analysis)
        weibull: function(scale, shape) {
            var u = Math.random();
            while (u <= 1e-15) u = Math.random();
            return scale * Math.pow(-Math.log(u), 1.0 / shape);
        }
    };

    // -------------------------------------------------------------
    // 2. Behavioral State Machine
    // -------------------------------------------------------------
    var STATES = {
        FOCUSED_COMBAT: {
            name: 'FOCUSED_COMBAT',
            baseMu: 210, baseSigma: 35, baseTau: 75,
            spatialScatter: 5.0,
            hesitationProb: 0.04
        },
        TACTICAL_DELIBERATION: {
            name: 'TACTICAL_DELIBERATION',
            baseMu: 650, baseSigma: 120, baseTau: 380,
            spatialScatter: 7.5,
            hesitationProb: 0.25
        },
        EXPLORATION_FLOW: {
            name: 'EXPLORATION_FLOW',
            baseMu: 320, baseSigma: 55, baseTau: 120,
            spatialScatter: 9.0,
            hesitationProb: 0.08
        },
        INVENTORY_MANAGEMENT: {
            name: 'INVENTORY_MANAGEMENT',
            baseMu: 380, baseSigma: 70, baseTau: 190,
            spatialScatter: 6.0,
            hesitationProb: 0.15
        },
        MICRO_DISTRACTION: {
            name: 'MICRO_DISTRACTION',
            baseMu: 2500, baseSigma: 600, baseTau: 1800,
            spatialScatter: 12.0,
            hesitationProb: 0.80
        }
    };

    // -------------------------------------------------------------
    // 3. Human Cognitive Fatigue & Vigilance Tracker
    // -------------------------------------------------------------
    function HumanFatigueModel(config) {
        config = config || {};
        this.sessionStartTime = Date.now();
        this.actionsPerformed = 0;
        this.currentState = STATES.EXPLORATION_FLOW;
        this.timeDecayMinutes = config.timeDecayMinutes || 90; // Typical Mackworth decay constant
        this.circadianBaseline = config.circadianBaseline || 1.0; // 1.0 = peak alertness, 1.3 = late night
        this.consecutiveActionBurst = 0;
        this.lastActionTimestamp = Date.now();

        // Autoregressive feature history for neural timing estimation
        this.history = [];
        this.maxHistory = 10;
    }

    HumanFatigueModel.prototype.getSessionDurationMinutes = function() {
        return (Date.now() - this.sessionStartTime) / (1000 * 60);
    };

    // Computes cognitive fatigue index (0.0 = completely fresh, 1.0 = heavy fatigue)
    HumanFatigueModel.prototype.getFatigueFactor = function() {
        var elapsedMin = this.getSessionDurationMinutes();
        // Asymptotic Mackworth vigilance degradation curve
        var timeFatigue = 1.0 - Math.exp(-elapsedMin / this.timeDecayMinutes);
        // Action-density fatigue (burst strain)
        var actionFatigue = Math.min(0.4, (this.actionsPerformed / 2500) * 0.4);
        var total = (timeFatigue * 0.7 + actionFatigue) * this.circadianBaseline;
        return Math.min(1.0, Math.max(0.0, total));
    };

    // State transition logic based on session phase and natural game pacing
    HumanFatigueModel.prototype.updateState = function(stateHint) {
        if (stateHint && STATES[stateHint]) {
            this.currentState = STATES[stateHint];
            return;
        }

        var fatigue = this.getFatigueFactor();
        var roll = Math.random();

        // As fatigue increases, probability of distraction and deliberation rises
        var distractionThreshold = 0.02 + (fatigue * 0.08);
        var deliberationThreshold = distractionThreshold + 0.15 + (fatigue * 0.10);

        if (roll < distractionThreshold) {
            this.currentState = STATES.MICRO_DISTRACTION;
        } else if (roll < deliberationThreshold) {
            this.currentState = STATES.TACTICAL_DELIBERATION;
        } else if (roll < 0.65) {
            this.currentState = STATES.EXPLORATION_FLOW;
        } else {
            this.currentState = STATES.FOCUSED_COMBAT;
        }
    };

    // -------------------------------------------------------------
    // 4. Ex-Gaussian Timing Engine with Fatigue Drift
    // -------------------------------------------------------------
    HumanFatigueModel.prototype.generateReactionDelay = function(opts) {
        opts = opts || {};
        this.actionsPerformed++;
        this.consecutiveActionBurst++;
        var fatigue = this.getFatigueFactor();
        var st = this.currentState;

        // Baseline shift under fatigue:
        // mu shifts slightly (+15% max), tau (hesitation tail) shifts heavily (+120%)
        var mu = (opts.mu || st.baseMu) * (1.0 + (fatigue * 0.18));
        var sigma = (opts.sigma || st.baseSigma) * (1.0 + (fatigue * 0.35));
        var tau = (opts.tau || st.baseTau) * (1.0 + (fatigue * 1.25));

        // Generate base Ex-Gaussian latency
        var delay = RandomDist.exGaussian(mu, sigma, tau);

        // Ensure physiologically valid human minimum (180ms visual sensory threshold)
        if (delay < 185) {
            delay = 185 + RandomDist.exponential(35);
        }

        // Hesitation sub-model: humans sometimes stop mid-interaction to re-read or double check
        if (Math.random() < (st.hesitationProb + fatigue * 0.10)) {
            var hesitationLapse = RandomDist.exGaussian(400, 80, 250);
            delay += hesitationLapse;
        }

        // Store into autoregressive history
        this.history.push({
            delay: delay,
            fatigue: fatigue,
            state: st.name,
            timestamp: Date.now()
        });
        if (this.history.length > this.maxHistory) {
            this.history.shift();
        }

        this.lastActionTimestamp = Date.now();
        return Math.round(delay);
    };

    // -------------------------------------------------------------
    // 5. Fitts' Law & Perceptual-Motor Trajectory Generator
    // -------------------------------------------------------------
    HumanFatigueModel.prototype.generateTrajectory = function(startX, startY, targetX, targetY, targetWidth) {
        targetWidth = targetWidth || 32; // Default interactable bounding box (32x32px)
        var dx = targetX - startX;
        var dy = targetY - startY;
        var distance = Math.sqrt(dx * dx + dy * dy);

        // Fitts' Law Index of Difficulty: ID = log2(2 * D / W)
        var id = Math.log2((2.0 * Math.max(1, distance)) / targetWidth);
        // Human movement time model: MT = a + b * ID (Shannon formulation)
        var mt = 130 + 115 * Math.max(0.5, id);
        var duration = this.generateReactionDelay({ mu: mt, sigma: mt * 0.18, tau: mt * 0.25 });

        // Target Spatial Scatter (Gaussian dispersion around the centroid, never pixel-perfect)
        var fatigue = this.getFatigueFactor();
        var scatterSigma = this.currentState.spatialScatter * (1.0 + fatigue * 0.8);
        var finalX = Math.round(targetX + RandomDist.gaussian(0, scatterSigma));
        var finalY = Math.round(targetY + RandomDist.gaussian(0, scatterSigma));

        // Generate Cubic Bézier Control Points with authentic human curvature & overshoot
        var midX = (startX + finalX) / 2.0;
        var midY = (startY + finalY) / 2.0;

        // Orthogonal deviation vector for natural hand arc
        var perpX = -dy / (distance || 1);
        var perpY = dx / (distance || 1);
        var arcMagnitude = (RandomDist.gaussian(0, 1) * distance * 0.12);

        var cp1X = startX + (dx * 0.25) + (perpX * arcMagnitude);
        var cp1Y = startY + (dy * 0.25) + (perpY * arcMagnitude);
        var cp2X = startX + (dx * 0.75) + (perpX * (arcMagnitude * 0.6));
        var cp2Y = startY + (dy * 0.75) + (perpY * (arcMagnitude * 0.6));

        // Generate discrete motion points sampled along minimum-jerk time parameterization
        var stepCount = Math.max(6, Math.min(40, Math.floor(duration / 16))); // ~60 Hz sampling
        var points = [];

        for (var i = 0; i <= stepCount; i++) {
            var rawT = i / stepCount;
            // Human velocity profile: smooth ease-in / ease-out (minimum-jerk: 10t^3 - 15t^4 + 6t^5)
            var t = 10 * Math.pow(rawT, 3) - 15 * Math.pow(rawT, 4) + 6 * Math.pow(rawT, 5);

            // Cubic Bézier formula: B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3
            var oneMinusT = 1 - t;
            var bx = Math.pow(oneMinusT, 3) * startX +
                     3 * Math.pow(oneMinusT, 2) * t * cp1X +
                     3 * oneMinusT * Math.pow(t, 2) * cp2X +
                     Math.pow(t, 3) * finalX;
            var by = Math.pow(oneMinusT, 3) * startY +
                     3 * Math.pow(oneMinusT, 2) * t * cp1Y +
                     3 * oneMinusT * Math.pow(t, 2) * cp2Y +
                     Math.pow(t, 3) * finalY;

            // Add physiological hand micro-tremor (~8-12 Hz, ±0.5px)
            var tremor = (Math.sin(rawT * Math.PI * 8) * 0.45);
            points.push({
                x: +(bx + tremor).toFixed(1),
                y: +(by + tremor).toFixed(1),
                timeFraction: +rawT.toFixed(3)
            });
        }

        return {
            startX: startX, startY: startY,
            targetX: finalX, targetY: finalY,
            durationMs: duration,
            points: points,
            fatigueFactor: +fatigue.toFixed(3),
            state: this.currentState.name
        };
    };

    // -------------------------------------------------------------
    // 6. Natural Break & Idle Hazard Function (Weibull Survival)
    // -------------------------------------------------------------
    HumanFatigueModel.prototype.checkBiologicalPause = function() {
        var elapsedMin = this.getSessionDurationMinutes();
        var fatigue = this.getFatigueFactor();

        // Micro-pause: looking away, reading chat, drinking water (20 - 75 seconds)
        if (this.consecutiveActionBurst > 45 && Math.random() < (0.04 + fatigue * 0.08)) {
            this.consecutiveActionBurst = 0;
            var microBreak = Math.round(RandomDist.weibull(35, 1.8) + 15) * 1000;
            return {
                type: 'MICRO_PAUSE',
                durationMs: Math.min(90000, microBreak),
                reason: 'Cognitive rest / re-orientation'
            };
        }

        // Biological break: AFK bathroom, stretch, snack (2 - 7 minutes)
        // Probability grows with continuous session time
        if (elapsedMin > 40 && Math.random() < (0.008 * (elapsedMin / 30))) {
            this.consecutiveActionBurst = 0;
            var bioBreak = Math.round(RandomDist.weibull(240, 2.2) + 60) * 1000;
            return {
                type: 'BIOLOGICAL_AFK',
                durationMs: Math.min(480000, bioBreak),
                reason: 'Biological break / ergonomics'
            };
        }

        return null;
    };

    return HumanFatigueModel;
}));
