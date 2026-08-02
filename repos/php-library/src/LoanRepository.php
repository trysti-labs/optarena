<?php

namespace App;

require_once __DIR__ . '/Loan.php';

class LoanRepository
{
    /** @var array<int, Loan> */
    private array $loans = [];
    private int $nextId = 1;

    public function create(int $bookId, int $memberId, string $status): Loan
    {
        $loan = new Loan($this->nextId, $bookId, $memberId, $status);
        $this->loans[$loan->id] = $loan;
        $this->nextId++;
        return $loan;
    }

    public function find(int $id): ?Loan
    {
        return $this->loans[$id] ?? null;
    }

    /** @return Loan[] */
    public function forBook(int $bookId): array
    {
        $loans = array_values(array_filter($this->loans, fn(Loan $l) => $l->bookId === $bookId));
        usort($loans, fn(Loan $a, Loan $b) => $a->id <=> $b->id);
        return $loans;
    }

    /** @return Loan[] */
    public function forMember(int $memberId): array
    {
        $loans = array_values(array_filter($this->loans, fn(Loan $l) => $l->memberId === $memberId));
        usort($loans, fn(Loan $a, Loan $b) => $a->id <=> $b->id);
        return $loans;
    }
}
