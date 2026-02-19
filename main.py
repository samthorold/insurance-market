from simulation import (
    experiment_underwriting_cycle,
    experiment_catastrophe_impact,
    experiment_network_evolution,
    experiment_lead_follow,
    experiment_path_dependence,
)


def main():
    print("Running experiments...")

    print("  1. Underwriting cycle...")
    r1 = experiment_underwriting_cycle()
    print(f"     Done. {len(r1)} seeds.")

    print("  2. Catastrophe impact...")
    r2 = experiment_catastrophe_impact()
    print(f"     Done.")

    print("  3. Network evolution...")
    r3_net, r3_prem = experiment_network_evolution()
    print(f"     Done. {len(r3_net)} year snapshots.")

    print("  4. Lead-follow comparison...")
    r4 = experiment_lead_follow()
    print(f"     Done.")

    print("  5. Path dependence...")
    r5 = experiment_path_dependence()
    print(f"     Done. {len(r5)} seeds.")

    print("All experiments complete.")


if __name__ == "__main__":
    main()
