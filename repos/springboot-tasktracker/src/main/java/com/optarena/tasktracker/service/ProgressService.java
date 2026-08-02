package com.optarena.tasktracker.service;

import com.optarena.tasktracker.dto.ProgressSummaryResponse;
import com.optarena.tasktracker.model.Task;
import com.optarena.tasktracker.model.TaskStatus;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class ProgressService {

    public ProgressSummaryResponse summarize(List<Task> tasks) {
        int total = tasks.size();
        long todo = tasks.stream().filter(t -> t.getStatus() == TaskStatus.TODO).count();
        long inProgress = tasks.stream().filter(t -> t.getStatus() == TaskStatus.IN_PROGRESS).count();
        long done = tasks.stream().filter(t -> t.getStatus() == TaskStatus.DONE).count();
        double percentDone = total == 0 ? 0.0 : Math.round(1000.0 * done / total) / 10.0;
        return new ProgressSummaryResponse(total, (int) todo, (int) inProgress, (int) done, percentDone);
    }
}
