import 'package:flutter/material.dart';

import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/job.dart';

class JobStatusChip extends StatelessWidget {
  const JobStatusChip(this.status, {super.key});

  final String status;

  @override
  Widget build(BuildContext context) {
    return StudioStatusPill(
      status: status,
      label: jobStatusLabels[status] ?? status,
    );
  }
}
