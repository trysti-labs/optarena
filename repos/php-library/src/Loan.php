<?php

namespace App;

class Loan
{
    public int $id;
    public int $bookId;
    public int $memberId;
    public string $status;

    public function __construct(int $id, int $bookId, int $memberId, string $status)
    {
        $this->id = $id;
        $this->bookId = $bookId;
        $this->memberId = $memberId;
        $this->status = $status;
    }
}
